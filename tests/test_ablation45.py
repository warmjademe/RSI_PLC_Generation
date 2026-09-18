"""Visibility, identity and accounting checks using synthetic model/tools."""
import copy
import json
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash
from our_method.ablation_workflow import ARMS, AblationContext, arm_config, run, visible_request
from tests import test_guarded_repair as fixtures
from tests.test_comparison import public_task


class Ablation45Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GuardedRepairTests(); self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def context(self, arm, responses, mode='pass'):
        original, _ = self.fixture.context(responses, mode=mode)
        original.output.rename(self.fixture.root/('fixture-template-'+arm))
        config = copy.deepcopy(original.config)
        config['budgets'] = {'max_candidates': 5, 'max_model_calls': 5}
        config['method'] = arm_config(config['method'], arm)
        ctx = AblationContext(public_task(), config, self.fixture.root/arm, method='OurMethod', arm=arm)
        return ctx, config['method']

    def test_full_visibility_keeps_actual_feedback_under_the_common_system(self):
        payload = {'task': {'id': 'demo'}, 'fixed_interface_st': 'FIXED', 'previous_code': 'CODE',
                   'base_code_sha256': content_hash('CODE'), 'memory': {'items': []},
                   'confirmed_errors': ['diagnostic'], 'repair_instruction': 'repair this error'}
        system, actual = visible_request('Full', 'original system', payload)
        self.assertEqual(actual, payload)
        self.assertEqual(system, visible_request('NoFeedback', 'other system', payload)[0])

    def test_empty_feedback_cannot_change_initial_prompt(self):
        payload = {'task': {'id': 'demo'}, 'fixed_interface_st': 'FIXED', 'previous_code': '',
                   'base_code_sha256': content_hash(''), 'memory': {'items': []},
                   'confirmed_errors': [], 'verification_uncertainty': [],
                   'task_local_attempts': {'items': [], 'instruction': 'generic history instruction'},
                   'repair_instruction': 'generic repair instruction', 'repeated_rejected_outputs': 0}
        requests = [visible_request(arm, 'arm dependent generic prompt', payload) for arm in ARMS]
        self.assertTrue(all(q == requests[0] for q in requests))
        self.assertEqual(set(requests[0][1]), {'task','fixed_interface_st','previous_code','base_code_sha256','memory'})

    def test_no_feedback_whitelist_drops_every_current_validation_channel(self):
        common = {'task': {'id': 'demo'}, 'fixed_interface_st': 'FIXED', 'previous_code': 'CODE',
                  'base_code_sha256': content_hash('CODE'), 'memory': {'items': []}}
        requests = []
        for status in ('fail', 'unknown'):
            payload = {**common, 'confirmed_errors': ['PRIVATE_'+status], 'verification_uncertainty': [status],
                       'repair_instruction': 'PRIVATE_instruction', 'task_local_attempts': ['PRIVATE_history'],
                       'unverified_task_draft': 'PRIVATE_draft', 'repeated_rejected_outputs': 3,
                       'required_response_representation': 'PRIVATE_format'}
            requests.append(visible_request('NoFeedback', 'PRIVATE_'+status, payload))
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[0][1], common)
        self.assertNotIn('PRIVATE_', json.dumps(requests))

    def test_asset_disabled_boundary_rejects_any_memory_item(self):
        for arm in ('NoAssets', 'Neither'):
            with self.assertRaises(ProtocolError):
                visible_request(arm, 'system', {'memory': {'items': [{'id': 'TRAINING_ASSET'}]}})

    def test_all_four_arms_check_fresh_code_and_keep_the_same_call_budget(self):
        response = {'code': 'CORRECT', 'base_code_sha256': content_hash('')}
        asset = (self.fixture.asset/'records.jsonl.gz').read_bytes()
        for arm in ARMS:
            with self.subTest(arm=arm):
                ctx, config = self.context(arm, [response])
                result = run(public_task(), ctx, config)
                self.assertEqual(result['status'], 'passed')
                self.assertEqual(result['budget']['model_calls'], 1)
                self.assertEqual(result['budget']['tool_calls'], 3)
                req = json.loads((ctx.output/'model/0001/request.json').read_text())
                self.assertEqual(req['payload']['previous_code'], '')
                if not ARMS[arm]['assets']:
                    self.assertEqual(req['payload']['memory']['items'], [])
                if not ARMS[arm]['feedback']:
                    self.assertNotIn('confirmed_errors', req['payload'])
                    self.assertNotIn('task_local_attempts', req['payload'])
        self.assertEqual((self.fixture.asset/'records.jsonl.gz').read_bytes(), asset)

    def test_feedback_disabled_still_records_failures_but_never_sends_them(self):
        responses = [{'code': 'BROKEN', 'base_code_sha256': content_hash('')},
                     {'code': 'CORRECT', 'base_code_sha256': content_hash('BROKEN')}]
        for arm in ('Full', 'NoFeedback'):
            ctx, config = self.context(arm, responses)
            original_check = ctx.check
            def check(stage, code):
                if code == 'BROKEN' and stage == 'compile':
                    receipt = {'stage': stage, 'status': 'fail', 'code_hash': content_hash(code),
                               'diagnostics': ['PRIVATE_CURRENT_ERROR'], 'evidence': {}}
                    ctx.record('synthetic_private_receipt', receipt=receipt)
                    return receipt
                return original_check(stage, code)
            ctx.check = check
            result = run(public_task(), ctx, config)
            self.assertEqual(result['status'], 'passed'); self.assertEqual(result['budget']['model_calls'], 2)
            req = (ctx.output/'model/0002/request.json').read_text()
            self.assertEqual('PRIVATE_CURRENT_ERROR' in req, arm == 'Full')
            self.assertIn('PRIVATE_CURRENT_ERROR', (ctx.output/'events.jsonl').read_text())
            self.assertEqual(json.loads(req)['payload']['previous_code'], 'BROKEN')

    def test_previous_study_seeds_and_wrong_arm_configuration_are_rejected(self):
        ctx, config = self.context('Full', [])
        ctx.resume_previous_code = 'OLD_STUDY_CODE'
        with self.assertRaises(ProtocolError): run(public_task(), ctx, config)
        ctx.resume_previous_code = ''
        with self.assertRaises(ProtocolError): run(public_task(), ctx, {**config, 'use_code_memory': False})
        self.assertEqual(ctx.budget.model_calls, 0)

    def test_disabled_feedback_is_not_used_for_repair_asset_retrieval(self):
        from unittest.mock import patch
        import our_method.guarded_workflow as workflow
        seen = []; original_retrieve = workflow.retrieve
        def observe(bank, task, feedback, config):
            seen.append(copy.deepcopy(feedback));return original_retrieve(bank, task, feedback, config)
        responses = [{'code': 'BROKEN', 'base_code_sha256': content_hash('')}]*5
        ctx, config = self.context('NoFeedback', responses, mode='fail')
        with patch.object(workflow, 'retrieve', side_effect=observe):run(public_task(), ctx, config)
        self.assertEqual(len(seen), 5);self.assertTrue(all(f == [] for f in seen))

    def test_paired_statistics_use_task_pairs_and_exact_discordance(self):
        from experiments.ablation45_study.audit import paired
        left = {str(i): {'success': i < 2} for i in range(4)}
        right = {str(i): {'success': False} for i in range(4)}
        result = paired(left, right, 'success', replicates=5000)
        self.assertEqual(result['mean_difference'], .5)
        self.assertEqual((result['left_only_success'], result['right_only_success']), (2, 0))
        self.assertEqual(result['exact_mcnemar_p'], .5)
        self.assertEqual(result, paired(left, right, 'success', replicates=5000))
        with self.assertRaises(ValueError): paired(left, {'other': {'success': False}}, 'success')

    def test_factorial_analysis_keeps_primary_contrasts_and_cost_direction(self):
        from experiments.ablation45_study.audit import analyze
        ids = [str(i) for i in range(45)]
        rows = [{'arm': arm, 'task_id': tid, 'success': arm == 'Full',
                 'budget': {'total_tokens': 100 if arm == 'Full' else 200}} for arm in ARMS for tid in ids]
        study = {'task_ids': ids, 'arms': ARMS, 'primary_paired_contrasts': [['Full','NoAssets'],['Full','NoFeedback']],
                 'bootstrap_seed': 7, 'bootstrap_replicates': 100, 'interpretation': 'synthetic'}
        result = analyze(rows, study)
        self.assertEqual(result['exploratory_success_interaction'], 1)
        for contrast in result['primary_contrasts']:
            self.assertEqual(contrast['success']['mean_difference'], 1)
            self.assertEqual(contrast['tokens_per_task']['mean_difference'], -100)
            self.assertGreaterEqual(contrast['success']['bh_adjusted_p'], contrast['success']['exact_mcnemar_p'])

    def test_pilot_usage_requires_settled_raw_accounting(self):
        from experiments.ablation45_study.pilot_drain import reconcile_usage
        budget={'model_calls':1,'candidates':1,'estimated_charge_calls':0,
                'input_tokens':10,'output_tokens':20,'total_tokens':30}
        responses=[{'usage':{'input_tokens':10,'output_tokens':20}}]
        wire=[{'generation_dispatched':True,'provider_usage':{'prompt_tokens':10,'completion_tokens':20},'returned_model':'deepseek-flash'}]
        self.assertEqual(reconcile_usage(budget,responses,wire)['total_tokens'],30)
        with self.assertRaises(ValueError):reconcile_usage(budget,responses,[])
        with self.assertRaises(ValueError):reconcile_usage({**budget,'model_calls':6},responses,wire)
        with self.assertRaises(ValueError):reconcile_usage({**budget,'total_tokens':29},responses,wire)


if __name__ == '__main__': unittest.main()
