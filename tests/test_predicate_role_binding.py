import copy
import json
import unittest

from experiments.historical_assets_study.study import generation_settings
from our_method.repair_retrieval import retrieve, source_bound_binding_obligations
from tests.test_repair_retrieval import bank, task


class PredicateRoleBindingTests(unittest.TestCase):
    def task(self):
        # The source latch is only an analogy here; this fixture checks the
        # admission/obligation boundary, not correctness for this numeric task.
        return {**task(), 'interface': {'inputs': {'N': 'INT'}, 'outputs': {'Out': 'BOOL'}}}

    def config(self):
        return {'repair_reference_view': 'after_only', 'repair_selection_view': 'contrast',
                'repair_boolean_role_binding': 'task_conditions'}

    def test_numeric_public_interface_is_not_changed_to_fit_a_source(self):
        records = bank(); query = self.task(); original = copy.deepcopy(query)
        self.assertFalse(retrieve(records, query, [], {})['items'])
        memory = retrieve(records, query, [], self.config())
        self.assertEqual(query, original)
        self.assertEqual(len(memory['items']), 1)
        obligation = memory['predicate_binding_obligations'][0]
        self.assertFalse(obligation['role_binding_established'])
        expected = sorted(r['slot'] for r in records[0]['roles']
                          if r['type'] == 'BOOL' and r['direction'] == 'VAR_INPUT')
        self.assertEqual(obligation['source_BOOL_input_roles'], expected)
        self.assertTrue(source_bound_binding_obligations(memory, {r['id']: r for r in records}, query, self.config()))
        obligation['source_BOOL_input_roles'] = []
        self.assertFalse(source_bound_binding_obligations(memory, {r['id']: r for r in records}, query, self.config()))

    def test_existing_bool_input_task_keeps_the_same_visible_packet(self):
        records = bank(); enabled = self.config(); prior = {k: v for k, v in enabled.items() if k != 'repair_boolean_role_binding'}
        self.assertEqual(retrieve(records, task(), [], enabled), retrieve(records, task(), [], prior))
        self.assertNotIn('predicate_binding_obligations', retrieve(records, task(), [], enabled))

    def test_numeric_output_width_is_not_relaxed(self):
        records = bank(); query = {**self.task(), 'interface': {'inputs': {'N': 'INT'}, 'outputs': {'Nout': 'DINT'}}}
        self.assertFalse(retrieve(records, query, [], self.config())['items'])

    def test_obligation_is_inside_the_fixed_character_budget(self):
        memory = retrieve(bank(), self.task(), [], self.config())
        size = len(json.dumps(memory, ensure_ascii=False))
        self.assertLessEqual(size, 4500)
        # This small synthetic after-only view is longer than its contrast
        # view. Fixed selection must fail closed, not emit an oversized packet.
        with self.assertRaisesRegex(ValueError, 'after-only view exceeds'):
            retrieve(bank(), self.task(), [], {**self.config(), 'memory_characters': max(1500, size-1)})

    def test_disabled_assets_and_feedback_do_not_gain_a_prompt_intervention(self):
        records = bank(); query = self.task(); config = self.config()
        disabled = {**config, 'use_code_memory': False}
        self.assertEqual(retrieve(records, query, [], disabled), retrieve(records, query, [], {'use_code_memory': False}))
        hidden = {**config, 'use_current_task_feedback': False}
        errors = [{'stage': 'compile', 'status': 'fail', 'diagnostics': ['PRIVATE_MARKER']}]
        self.assertEqual(retrieve(records, query, errors, hidden), retrieve(records, query, [], config))

    def test_unknown_policy_or_other_representation_is_rejected(self):
        with self.assertRaises(ValueError): retrieve(bank(), self.task(), [], {'repair_boolean_role_binding': 'anything'})
        with self.assertRaises(ValueError): generation_settings({'generation_settings': {'repair_boolean_role_binding': 'task_conditions'}})
        config = generation_settings({'generation_settings': {**self.config(), 'asset_representation': 'training_repair_slices_v2'}})
        self.assertEqual(config['repair_boolean_role_binding'], 'task_conditions')


if __name__ == '__main__': unittest.main()
