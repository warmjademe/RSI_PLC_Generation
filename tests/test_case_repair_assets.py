import copy
import unittest

from baseline_common.utils import content_hash
from our_method.case_projection import parsed_case_program
from our_method.repair_assets import learn
from our_method.repair_retrieval import project, source_bound_projection
from tests.test_repair_assets import fixture
from tests.test_typed_fragments import source


def case_repair():
    program, repair = fixture()
    for phase, value in [('before', 'TRUE'), ('after', 'Start')]:
        repair[phase+'_code'] = source(body=(
            'CASE Latch OF 0:Out:='+value+';Latch:=1;'
            ' 1:Out:=TRUE;Latch:=0;ELSE Out:=FALSE;Latch:=0;END_CASE;'))['code'].replace(
                'Latch : BOOL;', 'Latch : INT;')
    repair['observed_failure'][0]['evidence'] = [{'kind': 'formal_counterexample',
        'oracle_status': 'formal_counterexample_pending_runtime_replay',
        'trace': {'violated_condition': ['Out = Start']}}]
    return program, repair


class CaseRepairAssetsTests(unittest.TestCase):
    def test_opt_in_learns_bound_original_case_repairs(self):
        program, repair = case_repair()
        self.assertEqual(learn([program], [repair], slice_scope='cyclic_state')[0], [])
        assets, summary, graph = learn([program], [repair], declaration_scope='output_dependency',
                                       slice_scope='cyclic_state', source_syntax='scalar_if_case')
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        evidence = asset['evidence'][0]
        for phase in ('before', 'after'):
            self.assertEqual(evidence[phase+'_code_sha256'], content_hash(repair[phase+'_code']))
            self.assertEqual(evidence['source_control_normalization'][phase],
                             parsed_case_program(repair[phase+'_code'])[2])
        self.assertEqual(summary['contrasts_with_case_source_normalization'], 1)
        self.assertEqual(summary['witnessed_contrasts_with_case_source_normalization'], 1)
        self.assertFalse(asset['automatic_patch_allowed'])
        self.assertFalse(asset['transfer_generation_verified'])
        self.assertTrue(any(e['relation'] == 'reference_uses_source_normalization' for e in graph['edges']))

    def test_prompt_projection_names_normalization_without_losing_source_binding(self):
        program, repair = case_repair()
        asset = learn([program], [repair], slice_scope='cyclic_state', source_syntax='scalar_if_case')[0][0]
        evidence = asset['evidence'][0]
        item = project(asset, evidence, evidence['output_witnesses'])
        self.assertEqual(item['source_control_normalization']['phases_with_case'], ['before', 'after'])
        self.assertTrue(source_bound_projection(item, {asset['id']: asset}))
        item['source_control_normalization']['phases_with_case'] = []
        self.assertFalse(source_bound_projection(item, {asset['id']: asset}))

    def test_if_only_opt_in_preserves_asset_and_graph_content(self):
        program, repair = fixture()
        ordinary = learn([program], [repair], slice_scope='cyclic_state')
        enabled = learn([program], [repair], slice_scope='cyclic_state', source_syntax='scalar_if_case')
        self.assertEqual(enabled[0], ordinary[0])
        self.assertEqual(enabled[2], ordinary[2])
        self.assertEqual(enabled[1]['contrasts_with_case_source_normalization'], 0)

    def test_missing_immediate_success_still_rejects_case_repair(self):
        program, repair = case_repair()
        for status in ('unknown', 'fail'):
            item = copy.deepcopy(repair)
            item['after_feedback'][-1]['status'] = status
            self.assertEqual(learn([program], [item], source_syntax='scalar_if_case')[0], [])

    def test_unknown_syntax_is_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            learn([], [], source_syntax='arbitrary')


if __name__ == '__main__':
    unittest.main()
