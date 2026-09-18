import copy
import json
import unittest

from experiments.historical_assets_study.study import generation_settings
from our_method.repair_assets import learn
from our_method.repair_retrieval import project, retrieve, source_bound_projection
from our_method.typed_fragments import Parser, tokens
from tests.test_repair_retrieval import bank, task
from tests.test_repair_assets import fixture
from tests.test_typed_fragments import source


class RepairReferenceViewTests(unittest.TestCase):
    def test_after_view_preserves_source_roles_evidence_and_complete_code(self):
        asset = bank()[0]; original = copy.deepcopy(asset)
        evidence = asset['evidence'][0]
        before = project(asset, evidence, evidence['output_witnesses'])
        after = project(asset, evidence, evidence['output_witnesses'], view='after_only')
        self.assertNotIn('before_st', after)
        self.assertNotIn('entry_references_before', after)
        for key in ('after_st', 'roles', 'entry_references_after', 'source_context',
                    'source_failure_witness', 'after_source_checks', 'source_record_sha256'):
            self.assertEqual(after[key], before[key])
        self.assertTrue(source_bound_projection(after, {asset['id']: asset}))
        self.assertEqual(asset, original)
        for key in ('source_failure_witness', 'source_record_sha256', 'entry_references_after'):
            changed = copy.deepcopy(after); changed[key] = None
            self.assertFalse(source_bound_projection(changed, {asset['id']: asset}))
        changed = copy.deepcopy(after); changed['before_st'] = asset['before_st']
        self.assertFalse(source_bound_projection(changed, {asset['id']: asset}))

    def test_after_view_preserves_post_output_state_for_future_scans(self):
        program, repair = fixture()
        repair['before_code'] = source(body='Out := Latch; Latch := TRUE;')['code']
        repair['after_code'] = source(body='Out := Latch; Latch := Start;')['code']
        repair['observed_failure'][0]['evidence'] = [{'kind': 'formal_counterexample',
            'oracle_status': 'formal_counterexample_pending_runtime_replay',
            'trace': {'violated_condition': ['Out = Start']}}]
        asset = learn([program], [repair], declaration_scope='output_dependency',
                      slice_scope='cyclic_state')[0][0]
        evidence = asset['evidence'][0]
        item = project(asset, evidence, evidence['output_witnesses'], view='after_only')
        self.assertEqual(Parser(tokens(item['after_st'])).statements(), asset['after_tree'])
        self.assertEqual(item['retained_exit_roles_after'], asset['cyclic_dependency_roles_after'])
        self.assertEqual(len(item['retained_exit_roles_after']), 2)
        self.assertTrue(source_bound_projection(item, {asset['id']: asset}))
        item['after_st'] = item['after_st'].splitlines()[0]
        self.assertFalse(source_bound_projection(item, {asset['id']: asset}))

    def test_no_assets_and_no_feedback_controls_remain_isolated(self):
        records = bank(); config = {'repair_reference_view': 'after_only'}
        memory = retrieve(records, task(), [], config)
        self.assertTrue(memory['items'])
        self.assertNotIn('before_st', memory['items'][0])
        self.assertLessEqual(len(json.dumps(memory, ensure_ascii=False)), 4500)
        disabled = {**config, 'use_code_memory': False}
        self.assertEqual(retrieve(records, task(), [], disabled),
                         retrieve(records, task(), [], {'use_code_memory': False}))
        private = [{'stage': 'compile', 'status': 'fail', 'diagnostics': ['PRIVATE_MARKER']}]
        no_feedback = {**config, 'use_current_task_feedback': False}
        self.assertEqual(retrieve(records, task(), private, no_feedback), memory)
        self.assertNotIn('PRIVATE_MARKER', json.dumps(memory))

    def test_unknown_or_falsely_labeled_views_are_rejected(self):
        asset = bank()[0]; evidence = asset['evidence'][0]
        with self.assertRaises(ValueError):
            project(asset, evidence, evidence['output_witnesses'], view='arbitrary')
        with self.assertRaises(ValueError):
            retrieve([asset], task(), [], {'repair_reference_view': 'arbitrary'})
        item = project(asset, evidence, evidence['output_witnesses'])
        item['source_reference_view'] = 'after_only'
        self.assertFalse(source_bound_projection(item, {asset['id']: asset}))

    def test_explicit_setting_cannot_change_unrelated_representation(self):
        default = generation_settings({})
        self.assertNotIn('repair_reference_view', default)
        settings = {'asset_representation': 'training_repair_slices_v2',
                    'repair_reference_view': 'after_only'}
        self.assertEqual(generation_settings({'generation_settings': settings})['repair_reference_view'], 'after_only')
        for invalid in ({'repair_reference_view': 'after_only'},
                        {**settings, 'repair_reference_view': 'arbitrary'},
                        {**settings, 'repair_selection_view': 'arbitrary'},
                        {'repair_selection_view': 'contrast'},
                        {**settings, 'max_model_calls': 10}):
            with self.assertRaises(ValueError): generation_settings({'generation_settings': invalid})

    def test_fixed_selection_does_not_admit_previously_oversized_assets(self):
        small = bank()[0]; large = copy.deepcopy(small)
        large['id'] += '_large'
        large['before_st'] = small['before_st'] * 100
        large['before_tree'] = Parser(tokens(large['before_st'])).statements()
        records = [large, small]
        original = retrieve(records, task(), [], {})
        changed = retrieve(records, task(), [], {'repair_reference_view': 'after_only'})
        fixed = retrieve(records, task(), [], {'repair_reference_view': 'after_only',
                                              'repair_selection_view': 'contrast'})
        self.assertEqual(original['items'][0]['id'], small['id'])
        self.assertEqual(changed['items'][0]['id'], large['id'])
        self.assertEqual(fixed['items'][0]['id'], original['items'][0]['id'])
        self.assertEqual(fixed['items'][0]['after_st'], original['items'][0]['after_st'])
        self.assertNotIn('before_st', fixed['items'][0])
        self.assertTrue(source_bound_projection(fixed['items'][0], {r['id']: r for r in records}))


if __name__ == '__main__': unittest.main()
