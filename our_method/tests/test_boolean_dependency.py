import copy
import unittest

from baseline_common.errors import ProtocolError
from our_method.boolean_dependency import analyze
from our_method.tests import test_boolean_contract_rule as fixture


class BooleanDependency(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.BooleanRule();self.fixture.setUp()
        self.plan={'cases':[{'id':'scan','steps':[{'inputs':{'Permit':True}}]}]}

    def row(self,name,actual,expected):
        return {'case':'scan','step':0,'variable':name,'operator':'eq','observed':actual,'expected':expected,'matched':actual==expected}

    def evaluate(self,trace,plan=None):
        f=self.fixture
        return analyze(f.rule,f.metadata,f.code,plan or self.plan,trace)

    def test_correct_expression_with_wrong_dependency_is_distinguished(self):
        result=self.evaluate([self.row('Active',False,True),self.row('Denied',True,False)])
        row=result['decisions'][0]
        self.assertEqual(row['diagnosis'],'inspect_dependency')
        self.assertTrue(row['relation_holds_on_observed_values'])
        self.assertEqual(row['suspect_dependency'],'symbol:active')
        self.assertEqual(len(result['graph']['edges']),3)
        self.assertFalse(result['released_for_generation'])

    def test_local_expression_error_is_distinguished(self):
        result=self.evaluate([self.row('Active',True,True),self.row('Denied',True,False)])
        self.assertEqual(result['decisions'][0]['diagnosis'],'inspect_relation_implementation')

    def test_missing_values_never_default_to_false(self):
        row=self.evaluate([self.row('Denied',True,False)])['decisions'][0]
        self.assertEqual(row['diagnosis'],'insufficient_observations')
        plan={'cases':[{'id':'scan','steps':[{'inputs':{}}]}]}
        row=self.evaluate([self.row('Active',False,True),self.row('Denied',True,False)],plan)['decisions'][0]
        self.assertEqual(row['diagnosis'],'insufficient_observations')

    def test_unchanged_inputs_persist_across_steps_without_state_invention(self):
        plan=copy.deepcopy(self.plan);plan['cases'][0]['steps'].append({'inputs':{}})
        trace=[self.row('Active',False,True),self.row('Denied',True,False)]
        for r in trace:r['step']=1
        self.assertEqual(self.evaluate(trace,plan)['decisions'][0]['diagnosis'],'inspect_dependency')

    def test_conflicting_expectations_and_input_writes_are_rejected(self):
        trace=[self.row('Active',False,True),self.row('Active',False,False),self.row('Denied',True,False)]
        self.assertEqual(self.evaluate(trace)['decisions'][0]['diagnosis'],'insufficient_observations')
        f=self.fixture;code=f.code.replace('END_FUNCTION_BLOCK','Permit := FALSE;\nEND_FUNCTION_BLOCK')
        with self.assertRaises(ProtocolError):analyze(f.rule,f.metadata,code,self.plan,trace)


if __name__=='__main__':unittest.main()
