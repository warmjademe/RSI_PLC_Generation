import copy
import json
from pathlib import Path
import tempfile
import unittest

from baseline_common.binding import seal_adapter
from baseline_common.errors import ProtocolError
from baseline_common.memory import freeze
from baseline_common.utils import content_hash, object_hash, canonical_json
from our_method import knowledge_retrieval as retrieval
from our_method.ablation_workflow import AblationContext, arm_config, run, visible_request
from our_method.guarded_retrieval import retrieve as dispatch
from our_method.knowledge_candidates import candidate_record
from our_method.mechanism_retrieval import retrieve as legacy
from our_method.tests import test_knowledge_candidates as fixtures


class Boundary(Exception):
    pass


class Capture:
    kind = 'fixture'
    requested_model = 'fixture'
    secrets = []

    def complete(self, role, system, payload, **kwargs):
        self.request = {'role': role, 'system': system, 'payload': copy.deepcopy(payload)}
        raise Boundary()


class KnowledgeRetrieval(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.KnowledgeCandidates()
        fixture.setUp()
        self.protocol = fixture.protocol
        self.records = fixture.records
        for index, record in enumerate(self.records):
            record['metadata'] = {'contamination_group_id': f'source-group-{index}'}
        self.proposal = fixture.proposal
        self.bank = {r['id']: r for r in self.records}
        self.candidate = candidate_record(self.proposal, self.records, self.protocol)
        self.reference = retrieval.prepare_reference(self.candidate, self.bank, self.protocol)
        self.task = {'id': 'TE_QUERY', 'target': 'fixture',
                     'requirement': 'Reset the counter on reset with priority over increment.',
                     'interface': {'inputs': {'Reset': 'BOOL'}, 'outputs': {'Count': 'INT'}},
                     'files': {'main.st': ''}, 'entry_file': 'main.st', 'editable_files': ['main.st'],
                     'public_properties': []}
        self.config = {'asset_representation': retrieval.VERSION, 'use_code_memory': True,
                       'memory_characters': 6000, 'mechanism_top_k': 2}

    def packet(self, *, reference=None, task=None, feedback=None, config=None):
        return dispatch([reference or self.reference], task or self.task, feedback or [], config or self.config)

    def test_complete_knowledge_core_is_preserved_and_all_source_provenance_stays_in_bank(self):
        old = copy.deepcopy(self.reference)
        packet = self.packet()
        self.assertEqual(len(packet['items']), 1)
        core = packet['items'][0]['knowledge']
        self.assertEqual(core['payload'], self.proposal['payload'])
        self.assertEqual(core['boundaries'], self.proposal['boundaries'])
        for field in ['preconditions', 'verification_obligations']:
            self.assertEqual(core[field], [{k: v for k, v in row.items() if k != 'source_refs'}
                                          for row in self.proposal[field]])
        self.assertEqual(self.reference, old)
        self.assertEqual(self.reference['candidate']['proposal']['evidence'], self.proposal['evidence'])
        self.assertFalse(self.reference['candidate']['released_for_generation'])
        self.assertTrue(retrieval.source_bound_projection(packet['items'][0], {self.reference['id']: self.reference}))
        packet['items'][0]['knowledge']['payload']['then'] = 'Invented operation'
        self.assertFalse(retrieval.source_bound_projection(packet['items'][0], {self.reference['id']: self.reference}))

    def test_every_representation_keeps_its_full_state_or_relation_structure(self):
        cases = [
            ('state_machine', {'states': ['counter_idle', 'counter_reset'], 'initial': 'counter_idle',
                'transitions': [{'from': 'counter_idle', 'to': 'counter_reset', 'guard': 'Reset has priority',
                                 'action': 'Count := 0', 'source_refs': ['e1', 'e2']}]}),
            ('dependency_graph', {'entities': [{'id': 'reset', 'type': 'signal'}, {'id': 'counter', 'type': 'state'}],
                'relations': [{'from': 'reset', 'to': 'counter', 'relation': 'updates', 'source_refs': ['e1', 'e2']}]}),
            ('parameterized_template', {'parameters': ['reset', 'count'], 'st': 'IF reset THEN count := 0; END_IF;'}),
            ('procedure', {'steps': ['Establish reset priority.', 'Clear the counter.', 'Validate simultaneous updates.']})]
        for representation, payload in cases:
            with self.subTest(representation=representation):
                value = {**self.proposal, 'representation': representation, 'payload': payload}
                candidate = candidate_record(value, self.records, self.protocol)
                reference = retrieval.prepare_reference(candidate, self.bank, self.protocol)
                item = retrieval.project(reference)
                self.assertEqual(item['knowledge']['payload'], retrieval.without_trace_links(payload))
                self.assertTrue(retrieval.source_bound_projection(item, {reference['id']: reference}))

    def test_task_feedback_never_changes_knowledge_selection_or_bank(self):
        old = object_hash([self.records, self.reference])
        first = self.packet()
        for status in ['pass', 'fail', 'unknown']:
            feedback = [{'stage': 'formal', 'status': status, 'diagnostics': ['sensor motor temperature alarm']}]
            self.assertEqual(self.packet(feedback=feedback), first)
        self.assertEqual(old, object_hash([self.records, self.reference]))
        self.assertFalse(first['selection_audit']['selection_uses_current_feedback'])

    def test_disabled_assets_and_no_match_are_exact_legacy_empty_packets(self):
        config = {**self.config, 'use_code_memory': False}
        expected = legacy([], self.task, [], config)
        self.assertEqual(self.packet(config=config), expected)
        unrelated = {**self.task, 'requirement': 'Compute a floating point moving average of temperatures.',
                     'interface': {'inputs': {'Temperature': 'REAL'}, 'outputs': {'Average': 'REAL'}}}
        self.assertEqual(self.packet(task=unrelated), expected)

    def test_query_or_source_group_overlap_and_wrong_target_are_excluded(self):
        for change in [{'id': 'TR_1'}, {'metadata': {'contamination_group_id': 'source-group-0'}},
                       {'target': 'different-controller'}]:
            self.assertEqual(self.packet(task={**self.task, **change})['items'], [])

    def test_test_source_cannot_be_inserted_while_preparing_a_reference(self):
        changed = copy.deepcopy(self.bank)
        changed['TR_1']['learning_context_task_ids'] = ['TE_QUERY']
        value = copy.deepcopy(self.candidate)
        value['source_records']['TR_1'] = object_hash(changed['TR_1'])
        with self.assertRaises(ProtocolError):
            retrieval.prepare_reference(value, changed, self.protocol)

    def test_exact_quote_checks_run_again_before_reference_preparation(self):
        changed = copy.deepcopy(self.candidate)
        changed['proposal']['evidence'][0]['quote'] = 'A nonexistent counter reset operation.'
        with self.assertRaises(ProtocolError):
            retrieval.prepare_reference(changed, self.bank, self.protocol)

    def test_overlong_mechanism_is_omitted_whole_without_losing_boundaries(self):
        value = copy.deepcopy(self.proposal)
        value['boundaries'].append('Do not use outside these assumptions. '*100)
        candidate = candidate_record(value, self.records, self.protocol)
        reference = retrieval.prepare_reference(candidate, self.bank, self.protocol)
        packet = self.packet(reference=reference, config={**self.config, 'memory_characters': 1500})
        self.assertEqual(packet['items'], [])
        self.assertEqual(reference['candidate']['proposal']['boundaries'], value['boundaries'])
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False)), 1500)

    def test_negative_boundary_alone_does_not_trigger_irrelevant_reference(self):
        value = copy.deepcopy(self.proposal)
        value['boundaries'].append('Does not implement floating temperature averaging.')
        candidate = candidate_record(value, self.records, self.protocol)
        reference = retrieval.prepare_reference(candidate, self.bank, self.protocol)
        task = {**self.task, 'requirement': 'Floating temperature averaging.', 'interface': {}}
        self.assertEqual(self.packet(reference=reference, task=task)['items'], [])

    def test_deterministic_diversity_selects_complementary_terms_and_avoids_duplicate_core(self):
        value = copy.deepcopy(self.proposal)
        value['knowledge_need'] = 'Track motor temperature alarm.'
        value['payload'] = {'when': 'Motor temperature rises.', 'then': 'Check temperature alarm threshold.'}
        # This is a fixture hypothesis; source quotation validity does not prove
        # the claim. The experiment must establish actual learned usefulness.
        alternative = retrieval.prepare_reference(candidate_record(value, self.records, self.protocol),
                                                   self.bank, self.protocol)
        value2 = copy.deepcopy(self.proposal)
        value2['selection_reason'] += ' A duplicate source explanation.'
        duplicate = retrieval.prepare_reference(candidate_record(value2, self.records, self.protocol),
                                                 self.bank, self.protocol)
        bank = [self.reference, duplicate, alternative]
        task = {**self.task, 'requirement': self.task['requirement']+' Track motor temperature alarm.'}
        result = dispatch(bank, task, [], self.config)
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(result, dispatch(list(reversed(bank)), task, [], self.config))
        self.assertEqual(len({object_hash(i['knowledge']) for i in result['items']}), 2)

    def test_common_workflow_first_requests_respect_the_three_arm_intervention(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bank = root/'assets'
            freeze(bank, method='OurMethod', records=self.records+[self.reference], corpus={})
            seal_adapter(bank, 'OurMethod', ({}, self.records), {}, {})
            requests = {}
            for arm in ['Full', 'NoAssets', 'NoFeedback']:
                method = arm_config({**self.config, 'memory_root': str(bank)}, arm)
                config = {'method': method, 'provider': {}, 'validators': {},
                          'budgets': {'max_candidates': 5, 'max_model_calls': 5}}
                provider = Capture()
                ctx = AblationContext(self.task, config, root/arm, arm=arm, provider=provider)
                with self.assertRaises(Boundary):
                    run(self.task, ctx, method)
                requests[arm] = provider.request
                self.assertEqual(ctx.budget.tool_calls, 0)
                self.assertEqual(ctx.budget.model_calls, 1)
            self.assertEqual(requests['Full'], requests['NoFeedback'])
            self.assertTrue(requests['Full']['payload']['memory']['items'])
            self.assertFalse(requests['NoAssets']['payload']['memory']['items'])
            full_without_memory = copy.deepcopy(requests['Full'])
            full_without_memory['payload']['memory'] = requests['NoAssets']['payload']['memory']
            self.assertEqual(full_without_memory, requests['NoAssets'])
            feedback = {'confirmed_errors': [{'stage': 'runtime', 'diagnostics': ['task error']}],
                        'verification_uncertainty': [{'status': 'unknown'}]}
            payload = {**requests['Full']['payload'], **feedback}
            for arm in ['Full', 'NoFeedback']:
                _, visible = visible_request(arm, '', payload)
                self.assertEqual(visible['memory'], requests['Full']['payload']['memory'])
                self.assertEqual('confirmed_errors' in visible, arm == 'Full')

    def test_repeated_failed_code_keeps_the_same_knowledge_packet_on_the_third_request(self):
        class RepeatedCode(Capture):
            def __init__(self):
                self.requests = []

            def complete(self, role, system, payload, **kwargs):
                self.requests.append(copy.deepcopy(payload))
                if len(self.requests) == 3:
                    raise Boundary()
                return {'text': canonical_json({'code': 'Count := 0;', 'change_summary': 'fixture',
                                                'base_code_sha256': payload['base_code_sha256']}),
                        'model': 'fixture', 'invalid_finish': False,
                        'usage': {'input_tokens': 1, 'output_tokens': 1}}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bank = root/'assets'
            freeze(bank, method='OurMethod', records=self.records+[self.reference], corpus={})
            seal_adapter(bank, 'OurMethod', ({}, self.records), {}, {})
            method = arm_config({**self.config, 'memory_root': str(bank)}, 'Full')
            config = {'method': method, 'provider': {}, 'validators': {},
                      'budgets': {'max_candidates': 5, 'max_model_calls': 5}}
            provider = RepeatedCode()
            ctx = AblationContext(self.task, config, root/'run', arm='Full', provider=provider)
            ctx.check = lambda stage, code: {'stage': stage, 'status': 'fail',
                'code_hash': content_hash(code), 'diagnostics': ['fixture compiler rejection'], 'evidence': {}}
            with self.assertRaises(Boundary):
                run(self.task, ctx, method)
            self.assertEqual(len(provider.requests), 3)
            self.assertTrue(provider.requests[0]['memory']['items'])
            self.assertEqual(provider.requests[0]['memory'], provider.requests[1]['memory'])
            self.assertEqual(provider.requests[0]['memory'], provider.requests[2]['memory'])
            self.assertTrue(provider.requests[2]['confirmed_errors'])
            self.assertEqual(provider.requests[2]['repeated_rejected_outputs'], 1)


if __name__ == '__main__':
    unittest.main()
