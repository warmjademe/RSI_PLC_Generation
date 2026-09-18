import copy
import json
import unittest

from our_method.repair_assets import learn
from our_method.repair_retrieval import retrieve, source_bound_projection
from our_method.mechanism_retrieval import retrieve as legacy
from tests.test_repair_assets import fixture


def bank():
    p, r = fixture()
    r['observed_failure'][0]['evidence'] = [{'kind': 'formal_counterexample',
        'oracle_status': 'formal_counterexample_pending_runtime_replay',
        'trace': {'violated_condition': ['Reset -> NOT Out']}}]
    return learn([p], [r])[0]


def task():
    return {'id': 'TE_unseen', 'target': 'IEC_PORTABLE_ST',
            'requirement': 'Reset clears the latch output; Start sets the latch.',
            'interface': {'inputs': {'Reset': 'BOOL', 'Start': 'BOOL'}, 'outputs': {'Out': 'BOOL'}}}


class RepairRetrievalTests(unittest.TestCase):
    def test_whole_source_projection_and_character_budget(self):
        records = bank(); memory = retrieve(records, task(), [], {'memory_characters': 4500})
        self.assertEqual(len(memory['items']), 1)
        self.assertLessEqual(len(json.dumps(memory, ensure_ascii=False)), 4500)
        item = memory['items'][0]
        self.assertEqual(item['before_st'], records[0]['before_st'])
        self.assertEqual(item['after_st'], records[0]['after_st'])
        self.assertTrue(source_bound_projection(item, {r['id']: r for r in records}))
        item['after_st'] += ' TEST_DERIVED_PATCH'
        self.assertFalse(source_bound_projection(item, {r['id']: r for r in records}))

    def test_disabled_or_empty_memory_is_exactly_the_previous_control(self):
        records = bank(); config = {'use_code_memory': False, 'memory_characters': 4500}
        expected = legacy(records, task(), [], config)
        self.assertEqual(retrieve(records, task(), [{'status': 'fail', 'diagnostics': ['SECRET']}], config), expected)
        irrelevant = {**task(), 'requirement': 'Convert binary characters into hexadecimal digits.',
                      'interface': {'inputs': {'X': 'BOOL'}, 'outputs': {'Y': 'BOOL'}}}
        self.assertEqual(retrieve(records, irrelevant, [], {}), legacy(records, irrelevant, [], config))

    def test_feedback_disabled_never_routes_from_private_diagnostics(self):
        records = bank(); config = {'use_current_task_feedback': False}
        clean = retrieve(records, task(), [], config)
        hidden = retrieve(records, task(), [{'stage': 'compile', 'status': 'fail', 'diagnostics': ['TEST_SECRET']}], config)
        self.assertEqual(clean, hidden)
        self.assertNotIn('TEST_SECRET', json.dumps(hidden))

    def test_observed_failure_stage_selects_matching_source_evidence(self):
        records = bank()
        self.assertFalse(retrieve(records, task(), [{'stage': 'compile', 'status': 'fail'}], {})['items'])
        self.assertTrue(retrieve(records, task(), [{'stage': 'formal', 'status': 'fail'}], {})['items'])
        unknown = retrieve(records, task(), [{'stage': 'formal', 'status': 'unknown'}], {})
        self.assertFalse(unknown['selection_audit']['selection_uses_current_feedback'])

    def test_identity_group_and_output_type_filters(self):
        records = bank()
        for changed in ({'id': 'TR_one'}, {'metadata': {'contamination_group_id': 'g1'}},
                        {'interface': {'inputs': {'Reset': 'BOOL'}, 'outputs': {'Number': 'INT'}}}):
            self.assertEqual(retrieve(records, {**task(), **changed}, [], {})['items'], [])

    def test_nonlocalized_changes_and_oversized_whole_context_are_omitted(self):
        records = bank()
        records[0]['evidence'][0]['output_witnesses'] = []
        self.assertEqual(retrieve(records, task(), [], {})['items'], [])
        records = bank(); records[0]['after_st'] += 'x := FALSE;\n'*1000
        self.assertEqual(retrieve(records, task(), [], {'memory_characters': 1500})['items'], [])

    def test_memory_projection_cannot_mutate_the_frozen_bank(self):
        records = bank(); original = copy.deepcopy(records)
        memory = retrieve(records, task(), [], {})
        memory['items'][0]['source_failure_witness']['conditions'].append('ALTERED')
        memory['items'][0]['after_source_checks'].clear()
        self.assertEqual(records, original)


if __name__ == '__main__': unittest.main()
