import itertools
import unittest

from our_method.repair_assets import backward_slice, contrasts, learn, parsed_program
from our_method.repair_retrieval import project, source_bound_projection
from our_method.scan_state import cyclic_output_slice
from our_method.typed_fragments import Parser, tokens, variables
from tests.test_repair_assets import execute, fixture
from tests.test_typed_fragments import source


class ScanStateTests(unittest.TestCase):
    def assert_scan_outputs(self, code):
        fields, nodes = parsed_program(code)
        sliced, entry, closure = cyclic_output_slice(fields, nodes, {'out'})
        inputs = [n for n, f in fields.items() if f['direction'] == 'VAR_INPUT']
        state_names = [n for n in fields if n not in inputs]
        input_values = list(itertools.product((False, True), repeat=len(inputs)))
        for initial in itertools.product((False, True), repeat=len(state_names)):
            for sequence in itertools.product(input_values, repeat=3):
                full_state = dict(zip(state_names, initial)); slice_state = dict(full_state)
                for values in sequence:
                    incoming = dict(zip(inputs, values))
                    full_state = execute(nodes, {**full_state, **incoming})
                    slice_state = execute(sliced, {**slice_state, **incoming})
                    self.assertEqual(full_state['out'], slice_state['out'])
        return sliced, entry, closure

    def test_post_output_edge_memory_update_is_retained(self):
        code = source(body='Out := Start AND NOT Latch; Latch := Start;')['code']
        selected, _, closure = self.assert_scan_outputs(code)
        self.assertEqual(closure['retained_exit_targets'], ['latch', 'out'])
        self.assertEqual(len(selected), 2)
        _, nodes = parsed_program(code)
        one_scan, _ = backward_slice(nodes, {'out'})
        full = short = {'out': False, 'latch': False, 'start': True, 'reset': False}
        for _ in range(2):
            full = execute(nodes, full); short = execute(one_scan, short)
        self.assertFalse(full['out'])
        self.assertTrue(short['out'])

    def test_dependency_closure_follows_multiple_scan_delays(self):
        code = source(body='Out := Latch; Latch := Delay; Delay := Start;')['code'].replace(
            'Latch : BOOL;', 'Latch : BOOL; Delay : BOOL;')
        _, _, closure = self.assert_scan_outputs(code)
        self.assertEqual(closure['retained_exit_targets'], ['delay', 'latch', 'out'])
        self.assertEqual(closure['closure_iterations'], 3)

    def test_nested_reset_priority_is_preserved_across_scans(self):
        code = source(body='Out := Latch; IF Reset THEN Latch := FALSE; ELSIF Start THEN Latch := TRUE; END_IF;')['code']
        self.assert_scan_outputs(code)

    def test_unrelated_state_is_not_added_to_the_output_closure(self):
        code = source(body='Out := Latch; Latch := Start; Noise := NOT Noise;')['code'].replace(
            'Latch : BOOL;', 'Latch : BOOL; Noise : BOOL;')
        selected, entry, closure = self.assert_scan_outputs(code)
        reads, writes = variables(selected)
        self.assertNotIn('noise', reads+writes)
        self.assertNotIn('noise', closure['retained_exit_targets'])
        self.assertNotIn('noise', entry)

    def test_state_only_repair_can_change_future_outputs(self):
        program, repair = fixture()
        repair['before_code'] = source(body='Out := Latch; Latch := TRUE;')['code']
        repair['after_code'] = source(body='Out := Latch; Latch := Start;')['code']
        self.assertEqual(contrasts(repair, program, declaration_scope='output_dependency'), [])
        assets, summary, graph = learn([program], [repair], declaration_scope='output_dependency', slice_scope='cyclic_state')
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        self.assertEqual(asset['slice_scope'], 'cyclic_state')
        self.assertEqual(len(asset['cyclic_dependency_roles_before']), 2)
        self.assertEqual(summary['contrasts_retaining_extra_state_targets'], 1)
        self.assertFalse(asset['automatic_patch_allowed'])
        self.assertTrue(any(e['relation'] == 'retains_exit_for_cyclic_output_dependency' for e in graph['edges']))
        for phase in ('before', 'after'):
            self.assertTrue(set(asset['cyclic_dependency_roles_'+phase]) <=
                            set(asset[phase+'_transition']['exit_values']))

    def test_changed_persistent_state_declaration_is_not_transported(self):
        program, repair = fixture()
        repair['before_code'] = source(body='Out := Latch; Latch := TRUE;')['code'].replace(
            'Latch : BOOL;', 'Latch : BOOL := TRUE;')
        repair['after_code'] = source(body='Out := Latch; Latch := Start;')['code']
        self.assertEqual(contrasts(repair, program, declaration_scope='output_dependency', slice_scope='cyclic_state'), [])

    def test_unwritten_retained_state_has_an_explicit_identity_exit(self):
        program, repair = fixture()
        repair['before_code'] = source(body='Out := Latch;')['code']
        repair['after_code'] = source(body='Out := NOT Latch;')['code']
        asset = learn([program], [repair], declaration_scope='output_dependency', slice_scope='cyclic_state')[0][0]
        state = next(slot for slot, name in asset['evidence'][0]['binding'].items() if name == 'latch')
        for phase in ('before', 'after'):
            self.assertEqual(asset[phase+'_transition']['exit_values'][state], ['entry', state])

    def test_projection_carries_state_scope_and_remains_source_bound(self):
        program, repair = fixture()
        repair['observed_failure'][0]['evidence'] = [{'kind': 'formal_counterexample',
            'oracle_status': 'formal_counterexample_pending_runtime_replay',
            'trace': {'violated_condition': ['Reset -> NOT Out']}}]
        ordinary = learn([program], [repair], declaration_scope='output_dependency')[0][0]
        asset = learn([program], [repair], declaration_scope='output_dependency', slice_scope='cyclic_state')[0][0]
        self.assertNotEqual(asset['id'], ordinary['id'])
        evidence = asset['evidence'][0]
        item = project(asset, evidence, evidence['output_witnesses'])
        self.assertEqual(item['source_slice_scope'], 'cyclic_state_dependency_closure')
        self.assertEqual(item['source_projection'], 'complete_code_and_role_table_v1')
        roles = [dict(zip(item['role_columns'], row)) for row in item['roles']]
        self.assertEqual([role['role'] for role in roles], [role['slot'] for role in asset['roles']])
        for given, original in zip(roles, asset['roles']):
            self.assertEqual((given['type'], given['storage'], given['initial']),
                             (original['type'], original['direction'], original['initial']))
        for phase in ('before', 'after'):
            self.assertEqual(Parser(tokens(item[phase+'_st'])).statements(), asset[phase+'_tree'])
        self.assertTrue(source_bound_projection(item, {asset['id']: asset}))
        item['retained_exit_roles_before'] = []
        self.assertFalse(source_bound_projection(item, {asset['id']: asset}))

    def test_only_declared_outputs_can_start_a_closure(self):
        fields, nodes = parsed_program(source()['code'])
        for targets in [set(), {'start'}, {'latch'}, {'missing'}]:
            with self.assertRaises(ValueError): cyclic_output_slice(fields, nodes, targets)


if __name__ == '__main__': unittest.main()
