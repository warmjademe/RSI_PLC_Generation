import copy
import json
import tempfile
import unittest
from pathlib import Path

from baseline_common.memory import load
from baseline_common.binding import seal_adapter
from our_method.feedback import failure_stages
from our_method.repair_memory import compact_repair
from our_method.retrieval import retrieve
from our_method.training import train
from tests.test_comparison import sample_corpus, public_task


class RSIMemoryTests(unittest.TestCase):
    def bank(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'asset';train(sample_corpus(),path,{})
            return load(path,'OurMethod')[1]

    def test_nested_tool_failures_reach_repair_retrieval(self):
        bank=self.bank()
        feedback=[{'stage':'specification','status':'fail','evidence':{'tool_report':{'failed_gates':['compiler']}}}]
        self.assertIn('compiler',failure_stages(feedback))
        audit={};result=retrieve(bank,public_task(),feedback,{},audit=audit)
        self.assertTrue(any(x['kind']=='successful_trajectory_repair' for x in result['items']))
        self.assertEqual(audit['matching_repairs'],2)
        self.assertTrue(all('before_code' not in x for x in result['items']))

    def test_large_repair_keeps_bounded_actual_change_evidence(self):
        record=next(x for x in self.bank() if x['kind']=='successful_trajectory_repair')
        record['before_code']='\n'.join(f'Unused{i} := FALSE;' for i in range(2000))+'\nA_Run := A_Start;'
        record['after_code']=record['before_code'].replace('A_Run := A_Start;','A_Run := A_Start AND NOT A_Stop;')
        result=compact_repair(record,2400,[{'stage':'runtime','status':'fail','diagnostics':[{'variable':'A_Run'}]}])
        self.assertIsNotNone(result)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)),2400)
        self.assertIn('A_Run := A_Start AND NOT A_Stop;',str(result['change_excerpts']))
        self.assertEqual(len(result['before_sha256']),64)
        self.assertNotEqual(result['before_sha256'],result['after_sha256'])

    def test_contract_delta_ablation_preserves_selected_donors(self):
        bank=self.bank();task=public_task('Subsystem A shall satisfy: motor start stop.\nSubsystem B shall satisfy: safe output.')
        full=retrieve(bank,task,[],{})
        reduced=retrieve(bank,task,[],{'use_contract_delta':False})
        self.assertEqual([x['id'] for x in full['items']],[x['id'] for x in reduced['items']])
        self.assertTrue(all('contract_delta' not in x for x in reduced['items']))

    def test_memory_switches_and_global_budget_are_observed(self):
        bank=self.bank();feedback=[{'stage':'compile','status':'fail'}]
        for code in [False,True]:
            for repair in [False,True]:
                config={'use_code_memory':code,'use_repair_memory':repair,'memory_characters':10000}
                result=retrieve(bank,public_task(),feedback,config)
                kinds={x['kind'] for x in result['items']}
                self.assertEqual('verified_contract_donor' in kinds,code)
                self.assertEqual('successful_trajectory_repair' in kinds,repair)
                self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)),10000)

    def test_compact_projection_never_changes_raw_evidence(self):
        record=next(x for x in self.bank() if x['kind']=='successful_trajectory_repair');before=copy.deepcopy(record)
        self.assertIsNotNone(compact_repair(record,2800))
        self.assertEqual(record,before)
        record['after_code']=record['before_code']
        self.assertIsNone(compact_repair(record,2800))


if __name__=='__main__':unittest.main()
