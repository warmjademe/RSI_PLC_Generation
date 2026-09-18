import copy
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash
from our_method.boolean_contract_rule import equation_binding, propose_rule, apply_rule


class BooleanRule(unittest.TestCase):
    def setUp(self):
        self.code = '''FUNCTION_BLOCK Sample
VAR_INPUT Permit : BOOL; END_VAR
VAR_OUTPUT Active : BOOL; Denied : BOOL; END_VAR
IF Permit THEN Denied := FALSE; END_IF;
END_FUNCTION_BLOCK
'''
        self.metadata = {'target': {'model': 'fixture'}, 'scan': {'input_sampling': 'scan_start', 'output_observation': 'scan_end'},
            'interface': {'inputs': [{'name': 'Permit', 'type': 'BOOL'}],
                          'outputs': [{'name': 'Active', 'type': 'BOOL'}, {'name': 'Denied', 'type': 'BOOL'}]},
            'requirements': [{'id': 'R1', 'property': 'G(Denied = (Permit AND !Active))'}]}
        self.programs = [{'id': tid, 'kind': 'verified_program', 'task_id': tid, 'target': 'fixture',
                          'code': self.code, 'candidate_sha256': content_hash(self.code),
                          'metadata': copy.deepcopy(self.metadata)} for tid in ['TR_1', 'TR_2']]
        self.protocol = {'asset_source_ids': ['TR_1', 'TR_2'], 'protocol_sha256': 'fixture'}
        self.rule = propose_rule(self.programs, self.protocol)

    def test_roles_are_bound_without_training_variable_names(self):
        result, audit = apply_rule(self.code, self.metadata, self.rule)
        self.assertIn('Denied := Permit AND NOT Active;\nEND_FUNCTION_BLOCK', result)
        self.assertEqual(audit['roles'], {'output': 'Denied', 'request': 'Permit', 'ready': 'Active'})
        self.assertFalse(self.rule['released_for_generation'])

    def test_guarded_and_different_reset_predicates_cannot_match(self):
        for prop in ['G(Enable -> Denied = (Permit AND !Active))',
                     'G((Enable AND Ready) -> (Denied = (Permit AND !Active)))',
                     'G(Reset -> Value < HighLimit)', 'G(Denied = (Permit OR !Active))',
                     'G(Denied = (Permit AND !Active) OR Fault)',
                     'G(Denied = (Permit AND !Denied))']:
            self.assertIsNone(equation_binding(prop), prop)

    def test_equivalent_spelling_and_renaming(self):
        self.assertEqual(equation_binding('G((Out = (In AND NOT Ready)))'),
                         {'output': 'Out', 'request': 'In', 'ready': 'Ready'})

    def test_type_scan_target_and_direction_are_required(self):
        variants = []
        for change in ['type', 'scan', 'target', 'guard']:
            m = copy.deepcopy(self.metadata)
            if change == 'type': m['interface']['inputs'][0]['type'] = 'INT'
            elif change == 'scan': m['scan']['output_observation'] = 'scan_start'
            elif change == 'target': m['target'] = 'other'
            else: m['requirements'][0]['property'] = 'G(Enable -> (Denied = (Permit AND !Active)))'
            variants.append(m)
        for m in variants:
            with self.assertRaises(ProtocolError): apply_rule(self.code, m, self.rule)
        with self.assertRaises(ProtocolError):
            apply_rule(self.code.replace('VAR_OUTPUT', 'VAR'), self.metadata, self.rule)

    def test_output_reads_and_control_flow_escapes_are_rejected(self):
        for statement in ['Active := Denied;', 'IF Denied THEN Active := FALSE; END_IF;',
                          'RETURN;', 'JMP early;', "Denied := 'not code';"]:
            code = self.code.replace('END_FUNCTION_BLOCK', statement+'\nEND_FUNCTION_BLOCK')
            with self.assertRaises(ProtocolError): apply_rule(code, self.metadata, self.rule)

    def test_comment_mentions_do_not_count_as_reads(self):
        code = self.code.replace('IF Permit', '(* RETURN; Active := Denied; *)\nIF Permit')
        result, _ = apply_rule(code, self.metadata, self.rule)
        self.assertNotIn('RETURN', result)

    def test_sampled_input_roles_cannot_be_overwritten_before_scan_end(self):
        for statement in ['Permit := FALSE;', 'permit := NOT Permit;',
                          'Helper(Q => Permit);']:
            code = self.code.replace('END_FUNCTION_BLOCK',statement+'\nEND_FUNCTION_BLOCK')
            with self.assertRaisesRegex(ProtocolError,'sampled input role'):
                apply_rule(code,self.metadata,self.rule)
        metadata=copy.deepcopy(self.metadata)
        metadata['interface']['inputs'].append(metadata['interface']['outputs'].pop(0))
        code=self.code.replace('Permit : BOOL;', 'Permit : BOOL; Active : BOOL;').replace('VAR_OUTPUT Active : BOOL;', 'VAR_OUTPUT')
        code=code.replace('END_FUNCTION_BLOCK','Active := TRUE;\nEND_FUNCTION_BLOCK')
        with self.assertRaisesRegex(ProtocolError,'sampled input role'):
            apply_rule(code,metadata,self.rule)

    def test_unrelated_input_write_does_not_change_bound_role_checks(self):
        code=self.code.replace('Permit : BOOL;','Permit : BOOL; Unrelated : BOOL;')
        code=code.replace('END_FUNCTION_BLOCK','Unrelated := FALSE;\nEND_FUNCTION_BLOCK')
        changed,binding=apply_rule(code,self.metadata,self.rule)
        self.assertIn('Denied := Permit AND NOT Active;',changed)
        self.assertTrue(binding['conditions']['no_direct_input_role_write_or_output_binding'])

    def test_source_hash_and_group_isolation(self):
        for kind in ['hash', 'group', 'test']:
            records = copy.deepcopy(self.programs)
            if kind == 'hash': records[0]['code'] += 'modified'
            elif kind == 'group':
                for r in records: r['metadata']['contamination_group_id'] = 'same'
            else: records[0]['learning_context_task_ids'] = ['TE_1']
            with self.assertRaises(ProtocolError): propose_rule(records, self.protocol)


if __name__ == '__main__':
    unittest.main()
