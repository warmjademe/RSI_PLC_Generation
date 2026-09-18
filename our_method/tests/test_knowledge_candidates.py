import copy
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash
from our_method.knowledge_candidates import candidate_record, validate_proposal, source_documents


class KnowledgeCandidates(unittest.TestCase):
    def setUp(self):
        self.protocol = {'training_ids': ['TR_1', 'TR_2', 'TR_DEV'],
                         'asset_source_ids': ['TR_1', 'TR_2'], 'protocol_sha256': 'fixture'}
        code = 'IF Reset THEN Count := 0; END_IF;'
        self.records = [{'id': tid, 'kind': 'verified_program', 'task_id': tid,
                         'target': 'fixture', 'code': code, 'candidate_sha256': content_hash(code),
                         'requirement': 'Reset takes priority over all counter updates.',
                         'interface': 'VAR_INPUT Reset : BOOL; END_VAR'} for tid in ['TR_1', 'TR_2']]
        self.proposal = {'decision': 'propose', 'knowledge_need': 'Preserve reset priority.',
            'representation': 'conditional_rule', 'selection_reason': 'A local guarded operation is sufficient.',
            'preconditions': [{'statement': 'Reset has priority in the task contract.', 'check': 'unresolved',
                               'source_refs': ['e1', 'e2']}],
            'payload': {'when': 'Reset is active.', 'then': 'Clear the counter before other updates.'},
            'verification_obligations': [{'statement': 'Simultaneous reset and increment clears count.',
                                         'check': 'runtime', 'source_refs': ['e1', 'e2']}],
            'boundaries': ['Do not apply if reset is lower priority.'],
            'evidence': [{'id': 'e1', 'source_id': 'TR_1', 'field': 'code', 'quote': code},
                         {'id': 'e2', 'source_id': 'TR_2', 'field': 'code', 'quote': code}]}

    def test_valid_candidate_is_not_a_verified_or_released_asset(self):
        before = copy.deepcopy(self.proposal)
        result = candidate_record(self.proposal, self.records, self.protocol)
        self.assertEqual(result['status'], 'candidate')
        for key in ['semantic_entailment_verified', 'transfer_verified', 'released_for_generation']:
            self.assertIs(result[key], False)
        self.assertEqual(self.proposal, before)

    def test_test_and_development_direct_or_transitive_sources_are_rejected(self):
        for tid in ['TR_DEV', 'TE_1', 'v2-M-14']:
            for field in ['task_id', 'learning_context_task_ids']:
                with self.subTest(tid=tid, field=field):
                    records = copy.deepcopy(self.records)
                    records[0][field] = [tid] if field.endswith('_ids') else tid
                    with self.assertRaises(ProtocolError):
                        source_documents(records, self.protocol)

    def test_quotes_must_exist_in_the_exact_bound_field(self):
        for change in [{'quote': 'Reset ALWAYS clears every PLC output.'},
                       {'source_id': 'TR_missing'}, {'field': 'secret_file'}, {'quote': 'IF'}]:
            proposal = copy.deepcopy(self.proposal)
            proposal['evidence'][0].update(change)
            with self.assertRaises(ProtocolError):
                validate_proposal(proposal, self.records, self.protocol)

    def test_program_hash_cannot_change(self):
        self.records[0]['code'] += 'Count := 999;'
        with self.assertRaises(ProtocolError):
            validate_proposal(self.proposal, self.records, self.protocol)

    def test_unbound_or_unused_evidence_rejected(self):
        for mutate in ['missing', 'unused', 'single_program']:
            p = copy.deepcopy(self.proposal)
            if mutate == 'missing':
                p['preconditions'][0]['source_refs'] = ['invented']
            elif mutate == 'unused':
                p['evidence'].append({**p['evidence'][0], 'id': 'unused'})
            else:
                p['evidence'][1]['source_id'] = 'TR_1'
            with self.assertRaises(ProtocolError):
                validate_proposal(p, self.records, self.protocol)

    def test_graph_is_typed_and_all_endpoints_must_exist(self):
        p = copy.deepcopy(self.proposal)
        p['representation'] = 'dependency_graph'
        p['payload'] = {'entities': [{'id': 'reset', 'type': 'signal'}, {'id': 'count', 'type': 'state'}],
                        'relations': [{'from': 'reset', 'to': 'count', 'relation': 'updates', 'source_refs': ['e1', 'e2']}]}
        self.assertEqual(validate_proposal(p, self.records, self.protocol), p)
        for change in [{'to': 'invented'}, {'relation': 'guarantees_correctness'}]:
            bad = copy.deepcopy(p); bad['payload']['relations'][0].update(change)
            with self.assertRaises(ProtocolError):
                validate_proposal(bad, self.records, self.protocol)

    def test_state_machine_topology_and_other_representations(self):
        p = copy.deepcopy(self.proposal)
        for kind, payload in [
                ('state_machine', {'states': ['idle', 'counting'], 'initial': 'idle', 'transitions': [
                    {'from': 'counting', 'to': 'idle', 'guard': 'reset', 'action': 'count := 0', 'source_refs': ['e1', 'e2']}]}),
                ('parameterized_template', {'parameters': ['reset', 'count'], 'st': 'IF reset THEN count := 0; END_IF;'}),
                ('procedure', {'steps': ['Check priority.', 'Implement reset branch.', 'Validate simultaneous inputs.']})]:
            p.update(representation=kind, payload=payload)
            self.assertEqual(validate_proposal(p, self.records, self.protocol), p)
        p.update(representation='state_machine', payload={'states': ['idle'], 'initial': 'unknown', 'transitions': []})
        with self.assertRaises(ProtocolError):
            validate_proposal(p, self.records, self.protocol)

    def test_abstention_preserved_and_cannot_be_promoted_to_candidate(self):
        value = {'decision': 'abstain', 'reason': 'No supported shared mechanism.'}
        self.assertEqual(validate_proposal(value, self.records, self.protocol), value)
        with self.assertRaises(ProtocolError):
            candidate_record(value, self.records, self.protocol)

    def test_model_cannot_self_certify(self):
        self.proposal['released_for_generation'] = True
        with self.assertRaises(ProtocolError):
            validate_proposal(self.proposal, self.records, self.protocol)


if __name__ == '__main__':
    unittest.main()
