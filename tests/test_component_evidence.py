import copy
import unittest

from experiments.historical_assets_study.component_evidence import summarize_strata, control_variation


def fixture():
    rows = []; boundaries = []
    for arm in ('Full', 'NoAssets', 'NoFeedback'):
        for i in range(45):
            tid = 'TE_'+str(i); exposed = i < 25 and arm != 'NoAssets'
            rows.append({'arm': arm, 'task_id': tid, 'success': i != 0,
                         'judge_status': 'pass' if i != 0 else 'unknown',
                         'asset_exposed_calls': int(exposed), 'code_hash': 'code-'+tid,
                         'budget': {'model_calls': 1, 'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}})
            boundaries.append({'arm': arm, 'task_id': tid, 'asset_items': int(exposed),
                               'request_identity_hash': ('asset-' if exposed else 'empty-')+tid})
    report = {'complete': True, 'completed': 135, 'planned': 135, 'rows': rows,
              'arms': [{'arm': a, 'success': 44, 'completed_generation_tokens': 1350}
                       for a in ('Full', 'NoAssets', 'NoFeedback')]}
    ready = {'status': 'pass', 'first_request_boundaries': boundaries}
    audit = {'status': 'pass', 'completed': 135, 'assets_unchanged': True, 'test_feedback_learned': False}
    return report, ready, audit


class ComponentEvidenceTests(unittest.TestCase):
    def test_strata_use_prefrozen_exposure_and_keep_unknown_separate(self):
        data = fixture(); result = summarize_strata(*data)
        self.assertEqual([s['n'] for s in result['strata']], [25, 20])
        self.assertEqual(result['strata'][0]['arms']['Full']['unknown'], 1)
        self.assertEqual(result['strata'][0]['arms']['Full']['fail'], 0)
        self.assertEqual(result['strata'][1]['Full_NoAssets_identical_first_request_tasks'], 20)

    def test_later_exposure_does_not_reclassify_the_stratum(self):
        data = fixture()
        row = next(r for r in data[0]['rows'] if r['arm'] == 'Full' and r['task_id'] == 'TE_30')
        row['asset_exposed_calls'] = 1
        result = summarize_strata(*data)
        self.assertEqual([s['n'] for s in result['strata']], [25, 20])
        self.assertEqual(result['strata'][1]['initially_unexposed_Full_tasks_with_later_asset_exposure'], 1)

    def test_empty_asset_metadata_intervention_is_reported(self):
        data = fixture()
        for p in data[1]['first_request_boundaries']:
            if p['arm'] != 'NoAssets' and p['asset_items'] == 0: p['request_identity_hash'] += '-metadata'
        self.assertEqual(summarize_strata(*data)['strata'][1]['Full_NoAssets_identical_first_request_tasks'], 0)

    def test_partial_duplicate_and_changed_control_requests_are_rejected(self):
        data = fixture(); data[0]['complete'] = False
        with self.assertRaises(ValueError): summarize_strata(*data)
        data = fixture(); data[0]['rows'][1] = data[0]['rows'][0]
        with self.assertRaises(ValueError): summarize_strata(*data)
        old, new = fixture(), fixture()
        next(p for p in new[1]['first_request_boundaries'] if p['arm'] == 'NoAssets')['request_identity_hash'] += 'changed'
        with self.assertRaises(ValueError): control_variation(old, new)

    def test_control_repeat_variation_does_not_relabel_unknown_as_failure(self):
        old, new = fixture(), fixture()
        row = next(r for r in new[0]['rows'] if r['arm'] == 'NoAssets' and r['task_id'] == 'TE_0')
        row['judge_status'] = 'pass'; row['success'] = True
        next(a for a in new[0]['arms'] if a['arm'] == 'NoAssets')['success'] = 45
        result = control_variation(old, new)
        self.assertEqual(result['later_only_success'], 1)
        self.assertEqual(result['earlier_only_success'], 0)
        self.assertEqual(result['identical_final_code_but_different_verdict'], 1)
        self.assertEqual(result['different_final_code_hash'], 0)
        self.assertEqual(control_variation(old, copy.deepcopy(old))['later_only_success'], 0)


if __name__ == '__main__': unittest.main()
