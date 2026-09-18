"""Bounded inconclusive-code refactoring; synthetic tools, no PLC correctness claim."""
import copy
import json
import unittest

from our_method.guarded_workflow import run, refactorable_formal_timeout
from tests import test_guarded_repair as fixture
from tests.test_comparison import public_task


TIMEOUT = {'property_index': 1, 'status': 'unknown',
           'reason': 'PLCverif/backend exceeded its allocated property time.'}


class FormalTimeoutRefactor(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.GuardedRepairTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def context(self, responses, *, enabled=True, diagnostics=None):
        ctx, config = self.fixture.context([{'code': code} for code in responses])
        source = fixture.Path(fixture.__file__).with_name('fixture_validator.py').read_text()
        source = source.replace('    print(json.dumps(result))',
            "    if request['stage'] == 'formal' and request['code'] == 'TIMEOUT':\n"
            "        result['status'] = 'unknown'; result['diagnostics'] = "
            + repr([TIMEOUT] if diagnostics is None else diagnostics) + "\n"
            "    if request['stage'] == 'runtime' and request['code'] == 'RUNTIME_FAIL':\n"
            "        result['status'] = 'fail'; result['diagnostics'] = ['observed mismatch']\n"
            '    print(json.dumps(result))')
        script = self.fixture.root/'timeout_fixture.py'
        script.write_text(source)
        for stage in ('compile', 'runtime', 'formal'):
            ctx.config['validators'][stage]['command'][1] = str(script)
        if enabled is not None:
            config['refactor_formal_timeouts'] = enabled
        config['use_task_local_history'] = True
        return ctx, config

    def test_timeout_can_trigger_a_fresh_candidate_without_relabelling_receipts(self):
        ctx, config = self.context(['TIMEOUT', 'CORRECTED'])
        bank = (self.fixture.asset/'records.jsonl.gz').read_bytes()
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['budget']['model_calls'], 2)
        self.assertEqual(result['budget']['tool_calls'], 7)
        receipts = [json.loads(p.read_text()) for p in (ctx.output/'checks').glob('*/receipt.json')]
        self.assertEqual(sum(r['status'] == 'unknown' for r in receipts), 2)
        self.assertTrue(all(r['status'] == 'pass' for r in result['checks']))
        request = json.loads((ctx.output/'model/0002/request.json').read_text())
        payload = request['payload']
        self.assertEqual(payload['confirmed_errors'], [])
        self.assertEqual(payload['verification_uncertainty'][0]['status'], 'unknown')
        self.assertIn('structurally simpler', payload['repair_instruction'])
        self.assertIn('without claiming verified equivalence', payload['repair_instruction'])
        self.assertNotIn('Fix the concrete failing expression', request['system'])
        self.assertEqual((self.fixture.asset/'records.jsonl.gz').read_bytes(), bank)

    def test_default_and_disabled_modes_preserve_the_original_stop(self):
        for enabled in (None, False):
            with self.subTest(enabled=enabled):
                ctx, config = self.context(['TIMEOUT'], enabled=enabled)
                result = run(public_task(), ctx, config)
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['budget']['model_calls'], 1)
                self.assertEqual(result['budget']['tool_calls'], 4)
                (self.fixture.root/'run').rename(self.fixture.root/f'finished-{enabled}')

    def test_unrelated_or_mixed_unknowns_do_not_trigger_refactoring(self):
        other = {**TIMEOUT, 'reason': 'input_predicate portfolio did not complete a conclusive check.'}
        for index, diagnostics in enumerate(([other], [TIMEOUT, other], ['timeout'], [])):
            with self.subTest(diagnostics=diagnostics):
                ctx, config = self.context(['TIMEOUT'], diagnostics=diagnostics)
                result = run(public_task(), ctx, config)
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['budget']['model_calls'], 1)
                (self.fixture.root/'run').rename(self.fixture.root/f'finished-{index}')

    def test_repeated_timeout_remains_unknown_and_is_not_a_cached_failure(self):
        ctx, config = self.context(['TIMEOUT'] * 5)
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['budget']['model_calls'], 5)
        self.assertEqual(result['budget']['candidates'], 5)
        self.assertEqual(result['budget']['tool_calls'], 20)
        self.assertEqual(result['details']['duplicate_rejections'], 0)
        receipts = [json.loads(p.read_text()) for p in (ctx.output/'checks').glob('*/receipt.json')]
        self.assertEqual(sum(r['status'] == 'unknown' for r in receipts), 10)
        self.assertFalse(any(r['status'] == 'fail' for r in receipts))

    def test_changed_candidate_must_pass_runtime_again(self):
        ctx, config = self.context(['TIMEOUT', 'RUNTIME_FAIL', 'CORRECTED'])
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['budget']['model_calls'], 3)
        self.assertEqual(result['budget']['tool_calls'], 9)
        payload = json.loads((ctx.output/'model/0003/request.json').read_text())['payload']
        self.assertEqual(payload['confirmed_errors'][0]['stage'], 'runtime')
        self.assertNotIn('Compilation and the configured runtime tests passed', payload['repair_instruction'])

    def test_only_exact_timeout_after_current_passed_prerequisites_is_eligible(self):
        checks = [{'stage': s, 'status': 'pass', 'diagnostics': []} for s in ('compile', 'runtime')]
        checks.append({'stage': 'formal', 'status': 'unknown', 'diagnostics': [TIMEOUT]})
        self.assertTrue(refactorable_formal_timeout(checks))
        self.assertFalse(refactorable_formal_timeout(checks[1:]))
        self.assertFalse(refactorable_formal_timeout([checks[0], checks[2]]))
        for index in (0, 1, 2):
            for status in ('fail', 'error', 'unknown' if index != 2 else 'pass'):
                other = copy.deepcopy(checks); other[index]['status'] = status
                self.assertFalse(refactorable_formal_timeout(other))
        for index in (0, True, '1', None):
            other = copy.deepcopy(checks); other[-1]['diagnostics'][0]['property_index'] = index
            self.assertFalse(refactorable_formal_timeout(other))
        self.assertFalse(refactorable_formal_timeout(checks + [{'stage': 'runtime', 'status': 'unknown'}]))


if __name__ == '__main__':
    unittest.main()
