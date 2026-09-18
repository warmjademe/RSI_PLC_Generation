import copy
import itertools
import json
import unittest

from baseline_common.errors import ProtocolError
from our_method.repair_assets import backward_slice, learn, parsed_program, output_witnesses, contrasts
from our_method.typed_fragments import Parser, tokens, Unsupported
from tests.test_typed_fragments import source


def execute(nodes, initial):
    state = dict(initial)
    def expression(node):
        if node[0] == 'var': return state[node[1]]
        if node[0] == 'literal': return {'TRUE': True, 'FALSE': False}[node[1]]
        if node[0] == 'unary' and node[1] == 'NOT': return not expression(node[2])
        left, right = expression(node[2]), expression(node[3])
        return {'AND': lambda: left and right, 'OR': lambda: left or right,
                'XOR': lambda: left != right}[node[1]]()
    def sequence(items):
        for node in items:
            if node[0] == 'assign': state[node[1]] = expression(node[2])
            else:
                for condition, body in node[1]:
                    if expression(condition):
                        sequence(body)
                        break
                else: sequence(node[2])
    sequence(nodes)
    return state


def fixture(tid='TR_one', group='g1'):
    before = source(tid, group, 'IF Reset THEN Out := TRUE; ELSE Out := Start; END_IF;')
    after = source(tid, group, 'IF Reset THEN Out := FALSE; ELSE Out := Start; END_IF;')
    repair = {'id': 'repair:'+tid, 'kind': 'successful_trajectory_repair', 'task_id': tid,
              'target': after['target'], 'before_code': before['code'], 'after_code': after['code'],
              'trigger_stages': ['plcverif'], 'observed_failure': [{'name': 'plcverif', 'status': 'fail'}],
              'after_feedback': [{'name': name, 'status': 'pass'} for name in ('compiler', 'openplc_feedback', 'plcverif')],
              'repair_hypothesis': 'UNTRUSTED_REPAIR_PROSE_NOT_A_FACT'}
    return after, repair


class RepairAssetsTests(unittest.TestCase):
    def test_slice_preserves_output_over_all_boolean_valuations(self):
        bodies = [
            'Noise := A; IF Reset THEN Q := FALSE; ELSIF A THEN Q := TRUE; END_IF; Noise := B;',
            'IF A THEN Noise := TRUE; ELSIF B THEN Q := TRUE; ELSE Q := FALSE; END_IF;',
            'T := Q; Q := A; IF Reset THEN T := B; ELSE T := T AND Q; END_IF; Q := T;',
            'IF A THEN IF B THEN Q := TRUE; ELSE T := FALSE; END_IF; ELSE Q := FALSE; END_IF; Q := Q OR T;',
        ]
        names = ['a', 'b', 'reset', 'q', 't', 'noise']
        for body in bodies:
            nodes = Parser(tokens(body)).statements()
            sliced, incoming = backward_slice(nodes, {'q'})
            for values in itertools.product((False, True), repeat=len(names)):
                initial = dict(zip(names, values))
                self.assertEqual(execute(nodes, initial)['q'], execute(sliced, initial)['q'])
            self.assertTrue(incoming <= set(names))

    def test_early_empty_branch_keeps_priority_over_elsif(self):
        nodes = Parser(tokens('IF A THEN Noise := TRUE; ELSIF B THEN Q := TRUE; END_IF;')).statements()
        sliced, _ = backward_slice(nodes, {'q'})
        self.assertEqual(sliced[0][1][0], [['var', 'a'], []])
        self.assertFalse(execute(sliced, {'a': True, 'b': True, 'q': False})['q'])

    def test_observation_graph_has_bound_requirements_and_actual_changes(self):
        a, r = fixture(); b, s = fixture('TR_two', 'g2')
        assets, summary, graph = learn([a, b], [r, s])
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        self.assertEqual(asset['source_group_count'], 2)
        self.assertEqual(len(asset['evidence']), 2)
        self.assertNotEqual(asset['before_tree'], asset['after_tree'])
        self.assertNotIn('UNTRUSTED_REPAIR_PROSE_NOT_A_FACT', json.dumps(assets))
        self.assertTrue(all(e['source_requirements'] for e in asset['evidence']))
        self.assertFalse(asset['automatic_patch_allowed'])
        self.assertFalse(summary['transfer_efficacy_established'])
        nodes = {n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['from'] in nodes and e['to'] in nodes for e in graph['edges']))
        self.assertTrue(any(e['relation'] == 'subject_to_source_clause' for e in graph['edges']))
        self.assertEqual((assets, summary, graph), learn(copy.deepcopy([a, b]), copy.deepcopy([r, s])))

    def test_incomplete_or_unknown_following_checks_do_not_create_repair_asset(self):
        a, r = fixture()
        r['after_feedback'][-1]['status'] = 'unknown'
        self.assertEqual(learn([a], [r])[0], [])
        r['after_feedback'] = [{'name': 'compiler', 'status': 'pass'}]
        self.assertEqual(learn([a], [r])[0], [])

    def test_decl_changes_and_unsupported_code_are_not_silently_sliced(self):
        a, r = fixture()
        r['before_code'] = r['before_code'].replace('Latch : BOOL;', 'Latch : INT;')
        self.assertEqual(learn([a], [r])[0], [])
        with self.assertRaises(Unsupported): parsed_program(a['code'].replace('Out := FALSE;', 'Out := Fn(Start);'))

    def test_output_contract_is_required_and_model_prose_cannot_supply_it(self):
        a, r = fixture()
        a['metadata']['requirements'] = [{'text': 'Unrelated boilerplate.'}]
        self.assertEqual(learn([a], [r])[0], [])

    def test_output_scope_ignores_only_unreferenced_declaration_changes(self):
        a, r = fixture()
        r['before_code'] = r['before_code'].replace('Latch : BOOL;', 'Latch : INT;')
        self.assertEqual(learn([a], [r])[0], [])
        assets, summary, _ = learn([a], [r], declaration_scope='output_dependency')
        self.assertEqual(len(assets), 1)
        proof = assets[0]['evidence'][0]['declaration_compatibility']
        self.assertEqual(proof['changed_declarations_outside_both_slices'], ['latch'])
        self.assertTrue(proof['all_slice_role_declarations_equal'])
        self.assertFalse(proof['type_transport_performed'])
        self.assertEqual(summary['contrasts_from_changed_program_declarations'], 1)

    def test_output_scope_rejects_changed_guard_read_write_or_initialization(self):
        for name in ('Start', 'Reset', 'Out'):
            a, r = fixture()
            r['before_code'] = r['before_code'].replace(name+' : BOOL;', name+' : INT;')
            self.assertEqual(contrasts(r, a, declaration_scope='output_dependency'), [])
        a, r = fixture()
        r['before_code'] = r['before_code'].replace('Out : BOOL;', 'Out : BOOL := TRUE;')
        self.assertEqual(contrasts(r, a, declaration_scope='output_dependency'), [])

    def test_new_or_removed_unused_local_does_not_invent_a_role(self):
        a, r = fixture()
        r['after_code'] = r['after_code'].replace('Latch : BOOL;', 'Latch : BOOL; Unused : INT;')
        parts = contrasts(r, a, declaration_scope='output_dependency')
        self.assertEqual(len(parts), 1)
        self.assertNotIn('unused', parts[0]['source_binding'].values())
        self.assertEqual(parts[0]['declaration_compatibility']['changed_declarations_outside_both_slices'], ['unused'])

    def test_dependency_scope_keeps_entry_state_declarations(self):
        a, r = fixture()
        r['before_code'] = r['before_code'].replace('Out := Start;', 'Out := Latch;')
        r['after_code'] = r['after_code'].replace('Out := Start;', 'Out := Latch;')
        r['before_code'] = r['before_code'].replace('Latch : BOOL;', 'Latch : BOOL := TRUE;')
        self.assertEqual(contrasts(r, a, declaration_scope='output_dependency'), [])

    def test_unmodeled_new_output_is_still_counted_as_a_concurrent_edit(self):
        a, r = fixture()
        r['after_code'] = r['after_code'].replace('Out : BOOL;', 'Out : BOOL; Extra : BOOL;')
        parts = contrasts(r, a, declaration_scope='output_dependency')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['jointly_changed_output_slices'], 2)

    def test_nontraining_or_unbound_programs_are_rejected(self):
        a, r = fixture()
        with self.assertRaises(ProtocolError): learn([{**a, 'task_id': 'TE_test'}], [r])
        with self.assertRaises(ProtocolError): learn([{**a, 'candidate_sha256': 'invalid'}], [r])
        with self.assertRaises(ProtocolError): learn([a], [{**r, 'task_id': 'TE_test'}])

    def test_single_source_is_observation_and_derived_source_gets_no_credit(self):
        a, r = fixture(); a['origin'] = 'verified_cross_task_subsystems_then_deterministic_st_composition'
        assets, summary, _ = learn([a], [r])
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['source_group_count'], 0)
        self.assertEqual(assets[0]['observed_task_groups'], 1)
        self.assertEqual(summary['source_supported_by_multiple_credited_groups'], 0)
        self.assertFalse(assets[0]['upstream_lineage_complete'])

    def test_formal_witness_uses_whole_output_identifier_and_preserves_oracle_status(self):
        a, r = fixture()
        r['observed_failure'][0]['evidence'] = [{'kind': 'formal_counterexample',
            'oracle_status': 'formal_counterexample_pending_runtime_replay',
            'trace': {'violated_condition': ['Reset -> NOT Out', 'Reset -> NOT OutOther']}}]
        witnesses = output_witnesses(r, 'out')
        self.assertEqual(witnesses[0]['conditions'], ['Reset -> NOT Out'])
        self.assertEqual(witnesses[0]['oracle_status'], 'formal_counterexample_pending_runtime_replay')
        self.assertEqual(output_witnesses(r, 'unrelated'), [])
        assets, summary, graph = learn([a], [r])
        self.assertEqual(summary['contrasts_with_output_witness'], 1)
        self.assertEqual(assets[0]['witness_linked_source_groups'], 1)
        self.assertTrue(any(n['type'] == 'RecordedSourceOutputWitness' for n in graph['nodes']))

    def test_runtime_witness_ignores_unchanged_outputs_and_unstructured_prose(self):
        _, r = fixture()
        r['observed_failure'][0]['evidence'] = [{'kind': 'openplc_functional_failure',
            'trace': {'expected': {'Out': True, 'Other': False}, 'observed': {'Out': False, 'Other': False},
                      'case': 'source-fixture', 'step': 1}}]
        self.assertEqual(len(output_witnesses(r, 'out')), 1)
        self.assertEqual(output_witnesses(r, 'other'), [])
        r['observed_failure'][0]['evidence'] = 'Out failed according to a model'
        self.assertEqual(output_witnesses(r, 'out'), [])


if __name__ == '__main__': unittest.main()
