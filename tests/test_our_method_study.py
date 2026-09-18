import json
from pathlib import Path
import tempfile
import unittest

from experiments.deepseek_study.run import BASELINES, save, study_methods, test_one
from experiments.deepseek_study.report import report
from experiments.deepseek_study.setup_ours import setup_ours, sha
from experiments.deepseek_study.supervise import ready_work, validate_protocol


def paired_fixture(directory):
    base, ours = directory / 'baseline', directory / 'ours'
    ids = [f'TE_{i:02d}' for i in range(50)]
    manifest = base / 'source/test_dataset/manifest.jsonl'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(''.join(json.dumps({'id':tid}) + '\n' for tid in ids))
    save(manifest.with_name('checksums.json'), {'fixture':True})
    save(base / 'corpus/manifest.json', {'fixture':True})
    save(base / 'validator_reference/source.json', {'fixture':True})
    save(base / 'validator.json', {'tool_root':'fixed-tools','reference_root':'old','dataset_root':'old'})
    save(base / 'calibration/summary.json', {'status':'pass','task_count':50,'results':[
        {'task_id':tid,'status':'pass','reference':{'compile':'pass','runtime':'pass','formal':'pass'},
         'negative':{'compile':'pass','runtime':'fail'}} for tid in ids]})
    for name in ['validator.py','native_runtime.py','calibrate.py']:
        for root in [base, ours]:
            p = root / 'source/experiments/deepseek_study' / name
            p.parent.mkdir(parents=True, exist_ok=True);p.write_text('# fixed evaluator fixture\n')
    settings = {'memory_characters':24000,'memory_root':'placeholder','validation_stages':['compile','specification'],
                'use_code_memory':True,'use_repair_memory':True,'program_top_k':2,'repair_top_k':2}
    for stage, template in [('train','training_config.json'),('test','example_config.json')]:
        config = {'provider':{'slot_directory':str(base / 'slots'),'balance_state_file':str(base / 'balance.json')},
                  'budgets':{'max_candidates':5,'max_total_tokens':500000},'method':{'top_k':2}}
        if stage == 'test':
            config['validators'] = {'compile':{'command':['python','-m','validator','--settings','old'],
                                               'env':{'PYTHONPATH':'old'},'timeout_seconds':300}}
        save(base / 'configs' / f'FinalCodeRAG.{stage}.json', config)
        save(ours / 'source/our_method' / template, {'method':settings})
    save(base / 'study.json', {'methods':BASELINES,'training_tasks':1000,'test_tasks':50,
                              'max_candidates_per_method_task':5})
    save(base / 'source_snapshot_sha256.json', {})
    save(base / 'protocol.lock.json', {'study.json':sha(base / 'study.json')})
    return base, ours


class OurMethodStudyTests(unittest.TestCase):
    def test_paired_setup_preserves_base_and_copies_actual_limits(self):
        with tempfile.TemporaryDirectory() as d:
            base, ours = paired_fixture(Path(d))
            before = {str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()}
            setup_ours(ours, base)
            self.assertTrue(all(Path(p).read_bytes() == value for p,value in before.items()))
            config = json.loads((ours / 'configs/OurMethod.test.json').read_text())
            original = json.loads((base / 'configs/FinalCodeRAG.test.json').read_text())
            self.assertEqual(config['provider'], original['provider'])
            self.assertEqual(config['budgets'], original['budgets'])
            self.assertEqual(study_methods(ours), ['OurMethod'])
            self.assertEqual(study_methods(base), BASELINES)
            validate_protocol(ours)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                setup_ours(ours, base)

    def test_paired_setup_rejects_incomplete_or_changed_calibration(self):
        for change in ['reference_failed','missing_negative','evaluator_changed']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                base, ours = paired_fixture(Path(d))
                p = base / 'calibration/summary.json'
                doc = json.loads(p.read_text())
                if change == 'reference_failed':doc['results'][0]['reference']['runtime'] = 'fail'
                elif change == 'missing_negative':doc['results'][0]['negative']['runtime'] = 'pass'
                else:(ours / 'source/experiments/deepseek_study/validator.py').write_text('# changed\n')
                save(p, doc)
                with self.assertRaises(ValueError):setup_ours(ours, base)
                self.assertFalse((ours / 'study.json').exists())

    def test_ours_is_only_scheduled_after_its_own_asset_is_frozen(self):
        with tempfile.TemporaryDirectory() as d:
            base, ours = paired_fixture(Path(d));setup_ours(ours, base)
            save(ours / 'assets/FinalCodeRAG/adapter_manifest.json', {})
            self.assertEqual(ready_work(ours), [])
            save(ours / 'assets/OurMethod/adapter_manifest.json', {})
            self.assertEqual(len(ready_work(ours)),50)
            self.assertEqual({m for m,_ in ready_work(ours)}, {'OurMethod'})
            row = report(ours)['methods'][0]
            self.assertEqual(row['method'], 'OurMethod')
            self.assertEqual(row['planned_tasks'],50)
            self.assertIsNone(row['success_rate'])

    def test_ours_reuses_final_result_and_refuses_other_study_methods(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d);save(root / 'study.json', {'methods':['OurMethod']})
            expected = {'success':False,'budget':{'candidates':5}}
            save(root / 'tests/OurMethod/task1/summary.json', expected)
            self.assertEqual(test_one(root,'OurMethod','task1'),expected)
            with self.assertRaises(ValueError):test_one(root,'Vanilla','task1')
            self.assertFalse((root / 'tests/OurMethod/task1/generation').exists())

    def test_explicit_method_list_cannot_add_unknown_or_duplicate_methods(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for methods in [[],['OurMethod','OurMethod'],['unknown'],None,'OurMethod']:
                save(root / 'study.json', {'methods':methods})
                with self.assertRaises(ValueError):study_methods(root)


if __name__ == '__main__':unittest.main()
