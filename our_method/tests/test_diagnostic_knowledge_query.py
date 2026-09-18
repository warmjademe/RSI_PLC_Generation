import copy
from pathlib import Path
import tempfile
import unittest

from baseline_common.binding import seal_adapter, verify_adapter
from baseline_common.errors import ProtocolError
from baseline_common.memory import freeze
from baseline_common.utils import object_hash, canonical_json, content_hash
from our_method.ablation_workflow import AblationContext, arm_config, run
from our_method.diagnostic_knowledge_query import CURRENT_FEEDBACK, project_feedback, active_feedback
from our_method.knowledge_candidates import candidate_record
from our_method.knowledge_retrieval import prepare_reference, retrieve
from our_method.tests import test_knowledge_retrieval as fixtures
from our_method.tests.test_knowledge_retrieval import Capture, Boundary
from experiments.historical_assets_study.knowledge_protocol import verify_knowledge_payload


class DiagnosticKnowledgeQuery(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.KnowledgeRetrieval(); fixture.setUp()
        self.fixture = fixture
        self.task = fixture.task
        self.config = {**fixture.config, 'mechanism_top_k': 1, 'knowledge_query_mode': CURRENT_FEEDBACK}
        proposal = copy.deepcopy(fixture.proposal)
        # Scripted hypotheses establish selection behavior, not learned efficacy.
        proposal['knowledge_need'] = 'Counter behavior under overflow saturation.'
        proposal['payload'] = {'when': 'Counter overflow reaches saturation.',
                               'then': 'Keep counter within its declared range.'}
        proposal['preconditions'][0]['statement'] = 'Counter has a declared range.'
        alternative = prepare_reference(candidate_record(proposal, fixture.records, fixture.protocol),
                                        fixture.bank, fixture.protocol)
        self.bank = fixture.records+[fixture.reference, alternative]
        self.reset, self.overflow = fixture.reference['id'], alternative['id']
        self.feedback = [{'stage': 'runtime', 'status': 'fail',
                          'diagnostics': ['Counter overflow saturation diverged.']}]

    def test_current_failure_changes_retrieval_without_changing_source_bank_or_task(self):
        old = object_hash([self.bank, self.task, self.feedback])
        first = retrieve(self.bank, self.task, [], self.config)
        repaired = retrieve(self.bank, self.task, self.feedback, self.config)
        self.assertEqual(first['items'][0]['id'], self.reset)
        self.assertEqual(repaired['items'][0]['id'], self.overflow)
        self.assertTrue(repaired['selection_audit']['selection_uses_current_feedback'])
        self.assertEqual(repaired['selection_audit']['current_feedback_query_sha256'],
                         object_hash(project_feedback(self.feedback)))
        self.assertEqual(old, object_hash([self.bank, self.task, self.feedback]))
        self.assertNotIn('diverged', canonical_json(repaired))

    def test_ablation_disables_query_even_if_caller_supplies_diagnostics(self):
        first = retrieve(self.bank, self.task, [], self.config)
        self.assertEqual(retrieve(self.bank, self.task, self.feedback,
            {**self.config, 'use_current_task_feedback': False}), first)
        self.assertFalse(retrieve(self.bank, self.task, self.feedback,
            {**self.config, 'use_code_memory': False})['items'])
        self.assertEqual(retrieve(self.bank, self.task, self.feedback,
            {**self.config, 'knowledge_query_mode': 'public_only'}), first)

    def test_pass_format_messages_code_excerpts_and_evidence_are_not_query_inputs(self):
        first = retrieve(self.bank, self.task, [], self.config)
        for feedback in [
            [{**self.feedback[0], 'status': 'pass'}],
            [{**self.feedback[0], 'stage': 'response_format'}],
            [{'stage': 'compile', 'status': 'pass', 'diagnostics': [],
              'source_locations': self.feedback, 'evidence': {'message': self.feedback}}]]:
            self.assertEqual(retrieve(self.bank, self.task, feedback, self.config), first)
        before = retrieve(self.bank, self.task, self.feedback, self.config)
        with_noise = [{**self.feedback[0], 'source_locations': ['temperature pressure'],
                       'evidence': {'path': '/another/task'}, 'code': 'invented'}]
        self.assertEqual(retrieve(self.bank, self.task, with_noise, self.config), before)

    def test_unknown_keeps_its_status_and_does_not_become_a_confirmed_error(self):
        feedback = [{**self.feedback[0], 'status': 'unknown'}]
        projected = active_feedback(feedback, self.config)
        self.assertEqual(projected[0]['status'], 'unknown')
        projected[0]['diagnostics'].append('caller modification')
        self.assertNotIn('caller modification', feedback[0]['diagnostics'])
        self.assertNotEqual(object_hash(project_feedback(feedback)), object_hash(project_feedback(self.feedback)))

    def test_diagnostic_alone_cannot_bypass_public_target_or_identity_filters(self):
        for task in [{**self.task, 'requirement': 'Track velocity and pressure.', 'interface': {}},
                     {**self.task, 'target': 'different-controller'},
                     {**self.task, 'id': 'TR_1'},
                     {**self.task, 'metadata': {'contamination_group_id': 'source-group-0'}}]:
            self.assertFalse(retrieve(self.bank, task, self.feedback, self.config)['items'])

    def test_mode_is_explicit_and_ambiguous_stage_feedback_is_rejected(self):
        with self.assertRaises(ProtocolError):
            retrieve(self.bank, self.task, self.feedback, {**self.config, 'knowledge_query_mode': 'auto'})
        with self.assertRaises(ProtocolError):
            retrieve(self.bank, self.task, self.feedback*2, self.config)

    def test_visible_query_reconstruction_rejects_hidden_selection_and_query_tampering(self):
        payload = {'task': {k: self.task[k] for k in ('id', 'requirement', 'target', 'interface')},
                   'fixed_interface_st': '', 'confirmed_errors': self.feedback,
                   'memory': retrieve(self.bank, self.task, self.feedback, self.config)}
        self.assertTrue(verify_knowledge_payload(payload, self.task, self.bank, self.config))
        for mutate in [lambda p: p.pop('confirmed_errors'),
                       lambda p: p['confirmed_errors'][0].update(diagnostics=['temperature alarm']),
                       lambda p: p['memory']['selection_audit'].update(selection_uses_current_feedback=False)]:
            bad = copy.deepcopy(payload); mutate(bad)
            with self.assertRaises(ProtocolError):
                verify_knowledge_payload(bad, self.task, self.bank, self.config)
        with self.assertRaises(ProtocolError):
            verify_knowledge_payload(payload, self.task, self.bank,
                                     {**self.config, 'use_current_task_feedback': False})

    def test_actual_common_workflow_changes_only_full_retrieval_on_a_repair(self):
        class TwoRequests(Capture):
            def __init__(self): self.requests = []
            def complete(self, role, system, payload, **kwargs):
                self.requests.append(copy.deepcopy(payload))
                if len(self.requests) == 2: raise Boundary()
                return {'text': canonical_json({'code': 'Count := 0;', 'change_summary': 'fixture',
                            'base_code_sha256': payload['base_code_sha256']}),
                        'model': 'fixture', 'invalid_finish': False,
                        'usage': {'input_tokens': 1, 'output_tokens': 1}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bank = root/'assets'
            freeze(bank, method='OurMethod', records=self.bank, corpus={})
            seal_adapter(bank, 'OurMethod', ({}, self.fixture.records), {}, {})
            requests = {}
            for arm in ('Full', 'NoAssets', 'NoFeedback'):
                method = arm_config({**self.config, 'memory_root': str(bank)}, arm)
                config = {'method': method, 'provider': {}, 'validators': {},
                          'budgets': {'max_candidates': 5, 'max_model_calls': 5}}
                provider = TwoRequests()
                ctx = AblationContext(self.task, config, root/arm, arm=arm, provider=provider)
                ctx.check = lambda stage, code: {**self.feedback[0], 'stage': stage,
                    'code_hash': content_hash(code), 'evidence': {}}
                with self.assertRaises(Boundary): run(self.task, ctx, method)
                requests[arm] = provider.requests
                for payload in provider.requests:
                    verify_knowledge_payload(payload, self.task, self.bank, method)
                self.assertEqual(ctx.budget.model_calls, 2)
            self.assertEqual(requests['Full'][0], requests['NoFeedback'][0])
            self.assertEqual(requests['Full'][1]['memory']['items'][0]['id'], self.overflow)
            self.assertEqual(requests['NoFeedback'][1]['memory'], requests['NoFeedback'][0]['memory'])
            self.assertNotIn('confirmed_errors', requests['NoFeedback'][1])
            self.assertFalse(requests['NoAssets'][1]['memory']['items'])
            self.assertTrue(requests['NoAssets'][1]['confirmed_errors'])
            verify_adapter(bank, 'OurMethod')


if __name__ == '__main__': unittest.main()
