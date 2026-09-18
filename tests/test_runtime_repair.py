import copy
import json
import unittest

from our_method.guarded_workflow import run, repairable_runtime_termination
from tests import test_guarded_repair as fixture
from tests.test_comparison import public_task


class RuntimeRepair(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.GuardedRepairTests();self.fixture.setUp()

    def tearDown(self):self.fixture.tearDown()

    def context(self, responses, *, enabled=True, diagnostic='native runtime exited -8'):
        ctx, config = self.fixture.context(responses)
        script = self.fixture.root/'runtime_fixture.py'
        source = (fixture.Path(fixture.__file__).with_name('fixture_validator.py')).read_text()
        source = source.replace('    print(json.dumps(result))',
            "    if request['stage'] == 'runtime' and request['code'] == 'BROKEN':\n"
            "        result['status'] = 'unknown';result['diagnostics'] = "+repr([diagnostic])+"\n"
            '    print(json.dumps(result))')
        script.write_text(source)
        # Only the synthetic test validator is replaced; production oracles are untouched.
        ctx.config['validators']['runtime']['command'][1] = str(script)
        config['repair_runtime_crashes'] = enabled
        return ctx,config

    def test_signal_can_trigger_bounded_repair_without_changing_unknown_receipts(self):
        ctx,config = self.context([{'code':'BROKEN'},{'code':'CORRECTED'}])
        result = run(public_task(),ctx,config)
        self.assertEqual(result['status'],'passed');self.assertEqual(result['budget']['model_calls'],2)
        self.assertEqual(result['budget']['tool_calls'],6)
        receipts=[json.loads(p.read_text()) for p in (ctx.output/'checks').glob('*/receipt.json')]
        self.assertEqual(sum(r['status']=='unknown' for r in receipts),2)
        payload=json.loads((ctx.output/'model/0002/request.json').read_text())['payload']
        self.assertEqual(payload['verification_uncertainty'][0]['status'],'unknown')

    def test_other_unknowns_and_disabled_mode_stop_after_original_retry(self):
        for enabled,diagnostic in [(False,'native runtime exited -8'),(True,'timeout')]:
            with self.subTest(enabled=enabled,diagnostic=diagnostic):
                # Give each independent context a fresh output directory.
                if (self.fixture.root/'run').exists():
                    (self.fixture.root/'run').rename(self.fixture.root/f'previous-{enabled}')
                ctx,config=self.context([{'code':'BROKEN'}],enabled=enabled,diagnostic=diagnostic)
                result=run(public_task(),ctx,config)
                self.assertEqual(result['status'],'incomplete');self.assertEqual(result['budget']['model_calls'],1)
                self.assertEqual(result['budget']['tool_calls'],3)

    def test_repeated_crash_does_not_become_a_failure_or_consume_extra_tool_checks(self):
        ctx,config=self.context([{'code':'BROKEN'}]*5)
        result=run(public_task(),ctx,config)
        self.assertEqual(result['status'],'incomplete');self.assertEqual(result['budget']['model_calls'],5)
        self.assertEqual(result['budget']['tool_calls'],3)

    def test_only_exact_runtime_unknown_arithmetic_signal_is_eligible(self):
        check={'stage':'runtime','status':'unknown','diagnostics':['native runtime exited -8']}
        self.assertTrue(repairable_runtime_termination(check))
        for field,value in [('stage','formal'),('status','pass'),('diagnostics',['native runtime exited -9']),('diagnostics',['possible arithmetic issue'])]:
            other=copy.deepcopy(check);other[field]=value
            self.assertFalse(repairable_runtime_termination(other))


if __name__=='__main__':unittest.main()
