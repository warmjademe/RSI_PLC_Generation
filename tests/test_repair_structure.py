import copy
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import object_hash
from our_method.repair_assets import learn
from our_method.repair_structure import RepairStructureIndex
from our_method.typed_fragments import Unsupported
from tests.test_repair_assets import fixture
from tests.test_typed_fragments import source


def bank(body=None, after=None):
    program, repair = fixture()
    if body is not None:
        repair['before_code'] = source(body=body)['code']
    if after is not None:
        repair['after_code'] = source(body=after)['code']
    assets, _, _ = learn([program], [repair])
    return assets, repair['before_code']


class RepairStructureTests(unittest.TestCase):
    def test_renaming_and_case_preserve_match_without_learning_query(self):
        assets, code = bank()
        original = object_hash(assets)
        index = RepairStructureIndex(assets)
        snapshot = copy.deepcopy(index.__dict__)
        code = code.replace('Reset', 'Cancel').replace('Start', 'Enable').replace('Out', 'Ready').lower()
        matches = index.query(code)
        self.assertEqual(len(matches), 1)
        self.assertEqual(set(matches[0]['role_binding'].values()), {'cancel', 'enable', 'ready'})
        self.assertEqual(matches[0]['current_output'], 'ready')
        self.assertFalse(matches[0]['automatic_patch_allowed'])
        self.assertFalse(matches[0]['semantic_applicability_established'])
        matches[0]['role_binding']['p0'] = 'MUTATED_QUERY_RESULT'
        self.assertEqual(index.__dict__, snapshot)
        self.assertEqual(object_hash(assets), original)
        self.assertNotIn('MUTATED_QUERY_RESULT', str(index.query(code)))

    def test_type_storage_and_initialization_are_not_erased(self):
        assets, code = bank()
        index = RepairStructureIndex(assets)
        changes = [('Out : BOOL;', 'Out : INT;'),
                   ('Start : BOOL;', 'Start : BOOL := TRUE;'),
                   ('VAR_INPUT', 'VAR')]
        for old, new in changes:
            with self.subTest(change=new):
                self.assertEqual(index.query(code.replace(old, new)), [])

    def test_numeric_constants_operators_and_operand_aliasing_remain_distinct(self):
        assets, code = bank('Out := Start AND Reset;', 'Out := Start OR Reset;')
        index = RepairStructureIndex(assets)
        self.assertTrue(index.query(code))
        for changed in ['Start OR Reset', 'Start AND Start', 'NOT Start AND Reset']:
            self.assertEqual(index.query(code.replace('Start AND Reset', changed)), [])
        self.assertEqual(index.query(code.replace('Start AND Reset', 'TRUE')), [])
        # Constants retain exact lexical forms rather than an inferred range.
        assets, code = bank('Out := Start = (1 < 2);', 'Out := Start = (1 < 3);')
        self.assertTrue(RepairStructureIndex(assets).query(code))
        self.assertEqual(RepairStructureIndex(assets).query(code.replace('1 < 2', '1 < 3')), [])

    def test_entry_state_and_overwrite_order_are_not_dropped(self):
        assets, code = bank('Out := Latch; Latch := Start;', 'Out := Start; Latch := Start;')
        index = RepairStructureIndex(assets)
        self.assertTrue(index.query(code))
        self.assertEqual(index.query(code.replace('Out := Latch; Latch := Start;',
                                                  'Latch := Start; Out := Latch;')), [])
        self.assertEqual(index.query(code.replace('Latch : BOOL;', 'Latch : BOOL := TRUE;')), [])

    def test_empty_priority_arm_and_guards_are_preserved(self):
        body = 'IF Reset THEN Latch := TRUE; ELSIF Start THEN Out := TRUE; END_IF;'
        assets, code = bank(body, body.replace('Out := TRUE', 'Out := FALSE'))
        index = RepairStructureIndex(assets)
        self.assertTrue(index.query(code))
        reduced = code.replace(body, 'IF Start THEN Out := TRUE; END_IF;')
        self.assertEqual(index.query(reduced), [])

    def test_unrelated_statements_can_be_sliced_but_enclosing_guard_cannot(self):
        assets, code = bank()
        index = RepairStructureIndex(assets)
        self.assertTrue(index.query(code.replace('IF Reset', 'Latch := FALSE; IF Reset')))
        wrapped = code.replace('IF Reset', 'IF Latch THEN IF Reset').replace(
            'END_FUNCTION_BLOCK', 'END_IF; END_FUNCTION_BLOCK')
        self.assertEqual(index.query(wrapped), [])

    def test_after_only_source_roles_are_explicit_and_never_invented(self):
        assets, code = bank('Out := Start;', 'Out := Start AND Reset;')
        match = RepairStructureIndex(assets).query(code)[0]
        self.assertEqual(len(match['unbound_after_roles']), 1)
        self.assertFalse(set(match['unbound_after_roles']) & set(match['role_binding']))

    def test_unsupported_program_is_rejected_as_a_whole(self):
        assets, code = bank()
        index = RepairStructureIndex(assets)
        for statement in ['Latch := Fn(Start);', 'FOR i := 0 TO 2 DO Latch := Start; END_FOR;']:
            with self.assertRaises(Unsupported):
                index.query(code.replace('IF Reset', statement+' IF Reset'))

    def test_no_empty_or_nontraining_trigger(self):
        assets, _ = bank('Latch := Start;', 'Out := Start;')
        index = RepairStructureIndex(assets)
        self.assertEqual(index.assets_indexed, 0)
        self.assertEqual(index.empty_antecedents, 1)
        assets[0]['source_task_ids'] = ['TE_test']
        with self.assertRaises(ProtocolError): RepairStructureIndex(assets)


if __name__ == '__main__': unittest.main()
