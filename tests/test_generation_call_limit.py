import copy
import unittest

from baseline_common import RunContext
from experiments.hard45_study.prepare_next import set_call_limit
from our_method.guarded_workflow import run
from tests import test_guarded_repair as fixture
from tests.test_comparison import public_task


class GenerationLimit(unittest.TestCase):
    def test_explicit_one_call_and_inherited_limits(self):
        config={'budgets':{'max_model_calls':5,'max_candidates':5,'max_output_tokens':65536}}
        self.assertEqual(set_call_limit(config,1),1)
        self.assertEqual(config['budgets']['max_model_calls'],config['budgets']['max_candidates'])
        self.assertEqual(set_call_limit(config,None),1)
        self.assertEqual(config['budgets']['max_output_tokens'],65536)

    def test_invalid_limits_are_rejected_before_mutation(self):
        for limit in [0,6,True,1.5,'1']:
            config={'budgets':{'max_model_calls':5,'max_candidates':5}}
            original=copy.deepcopy(config)
            with self.assertRaises(ValueError):set_call_limit(config,limit)
            self.assertEqual(config,original)

    def test_single_call_stops_after_failure_and_preserves_declared_budget(self):
        f=fixture.GuardedRepairTests();f.setUp()
        try:
            previous,_=f.context([{'code':'FIRST'},{'code':'SECOND'}],mode='fail')
            config=copy.deepcopy(previous.config);config['budgets']={'max_model_calls':5,'max_candidates':5,'max_output_tokens':65536}
            set_call_limit(config,1)
            ctx=RunContext(public_task(),config,f.root/'limited_run',method='OurMethod')
            result=run(public_task(),ctx,config['method'])
            self.assertEqual(result['budget']['model_calls'],1)
            self.assertEqual(result['budget']['candidates'],1)
            self.assertIn('limit 1',result['reason'])
            self.assertFalse((ctx.output/'model/0002/request.json').exists())
        finally:f.tearDown()


if __name__=='__main__':unittest.main()
