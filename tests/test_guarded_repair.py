"""Protocol tests use synthetic training data and scripted model/tool outputs."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from baseline_common import RunContext
from baseline_common.binding import seal_adapter
from baseline_common.memory import load
from our_method.guarded_retrieval import locally_verified, retrieve
from our_method.guarded_workflow import run
from our_method.training import train
from tests.test_comparison import sample_corpus, public_task


class GuardedRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.asset = self.root / 'asset'
        corpus = sample_corpus()
        summary = train(corpus, self.asset, {})
        seal_adapter(self.asset, 'OurMethod', corpus, {}, summary)
        self.bank = load(self.asset, 'OurMethod')[1]
        self.repair = copy.deepcopy(next(r for r in self.bank if r['kind'] == 'successful_trajectory_repair'))
        self.repair['after_feedback'] = [{'name': n, 'status': 'pass'} for n in
                                         ('compiler', 'plcverif', 'openplc_feedback')]

    def tearDown(self):
        self.tmp.cleanup()

    def context(self, responses, mode='pass'):
        config = {'method': {'memory_root': str(self.asset)},
                  'provider': {'kind': 'replay', 'responses': [
                      {'role': 'plc.generate', 'response': r} for r in responses]},
                  'validators': {s: {'kind': 'command', 'protocol': 'json', 'test_only': True,
                                    'command': [sys.executable, str(Path(__file__).with_name('fixture_validator.py')), mode]}
                                 for s in ('compile', 'runtime', 'formal')}}
        return RunContext(public_task(), config, self.root / 'run', method='OurMethod'), config['method']

    def test_repair_requires_changed_code_failure_and_complete_recorded_stage_coverage(self):
        self.assertTrue(locally_verified(self.repair))
        for stage in ('compiler', 'plcverif', 'openplc_feedback'):
            record = copy.deepcopy(self.repair)
            record['after_feedback'] = [g for g in record['after_feedback'] if g['name'] != stage]
            self.assertFalse(locally_verified(record))
        for status in ('fail', 'unknown', 'skipped'):
            record = copy.deepcopy(self.repair)
            record['after_feedback'].append({'name': 'openplc_confirmation', 'status': status})
            self.assertFalse(locally_verified(record))
        record = copy.deepcopy(self.repair); record['after_code'] = record['before_code']
        self.assertFalse(locally_verified(record))
        record = copy.deepcopy(self.repair); record['observed_failure'] = []
        self.assertFalse(locally_verified(record))

    def test_retrieval_audit_matches_sent_memory_and_preserves_source(self):
        bank = [self.repair]; original = copy.deepcopy(bank)
        feedback = [{'stage': 'compile', 'status': 'fail', 'diagnostics': ['syntax error']}]
        result = retrieve(bank, public_task(), feedback, {})
        self.assertEqual(result['selection_audit']['sent_repairs'], 1)
        self.assertNotIn('before_code', result['items'][0])
        self.assertTrue(result['items'][0]['change_excerpts'])
        self.assertEqual(bank, original)
        for config in ({'use_repair_memory': False}, {'repair_top_k': 0}, {'memory_characters': 1024}):
            packet = retrieve(bank, public_task(), feedback, config)
            self.assertEqual(packet['selection_audit']['sent_repairs'], len(packet['items']))
            self.assertEqual(packet['items'], [])
            self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False)), config.get('memory_characters', 16000))

    def test_code_ablation_and_unrecognized_contract_omit_program_donors(self):
        self.assertFalse(retrieve(self.bank, public_task(), [], {})['items'])
        task = public_task('Subsystem A shall satisfy: motor start stop.\nSubsystem B shall satisfy: safe output.')
        self.assertTrue(retrieve(self.bank, task, [], {})['items'])
        self.assertFalse(retrieve(self.bank, task, [], {'use_code_memory': False})['items'])

    def test_duplicate_known_failure_consumes_candidate_but_not_validation(self):
        ctx, config = self.context([{'code': 'BROKEN'}] * 5)
        ctx.resume_previous_code = 'BROKEN'
        ctx.resume_feedback = [{'stage': 'compile', 'status': 'fail', 'diagnostics': ['syntax error']}]
        before = (self.asset / 'records.jsonl.gz').read_bytes()
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['budget']['candidates'], 5)
        self.assertEqual(result['budget']['model_calls'], 5)
        self.assertEqual(result['budget']['tool_calls'], 0)
        self.assertEqual((self.asset / 'records.jsonl.gz').read_bytes(), before)

    def test_repeated_unknown_seed_is_checked_and_never_cached_as_failure(self):
        ctx, config = self.context([{'code': 'UNCHANGED'}])
        ctx.resume_previous_code = 'UNCHANGED'
        ctx.resume_feedback = [{'stage': 'formal', 'status': 'unknown', 'diagnostics': ['timeout']}]
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['budget']['tool_calls'], 3)

    def test_late_format_rejection_does_not_reuse_previous_candidate_receipts(self):
        ctx, config = self.context([{'code': 'FIRST'}] + [{}] * 4, mode='fail')
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['budget']['model_calls'], 5)
        self.assertEqual(result['checks'], [])
        payload = json.loads((ctx.output / 'model/0005/request.json').read_text())['payload']
        self.assertTrue(any(x['stage'] == 'compile' for x in payload['confirmed_errors']))


if __name__ == '__main__':
    unittest.main()
