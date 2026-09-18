import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ablation45_study import report_final as report


def examples():
    ids=[str(i) for i in range(45)]
    rows=[{'arm':a,'task_id':tid,'success':False,'judge_status':'unknown','first_candidate_success':False,
           'asset_exposed_calls':0,'budget':{'model_calls':1,'input_tokens':30,'output_tokens':70,'total_tokens':100,'elapsed_seconds':1.5}}
          for a in report.ARMS for tid in ids]
    return ids,rows


class FinalReportTests(unittest.TestCase):
    def test_failure_and_unknown_costs_are_retained(self):
        ids,rows=examples()
        rows[0].update(success=True,judge_status='pass',first_candidate_success=True)
        rows[1]['judge_status']='fail'
        rows[2]['budget'].update(input_tokens=3000,output_tokens=7000,total_tokens=10000)
        full=report.aggregate(rows,ids)[0]
        self.assertEqual((full['success'],full['fail'],full['unknown']),(1,1,43))
        self.assertEqual(full['total_tokens'],14400)
        self.assertEqual(full['total_tokens_per_success'],14400)

    def test_no_success_yields_no_cost_per_success_estimate(self):
        ids,rows=examples()
        for group in report.aggregate(rows,ids):
            self.assertIsNone(group['total_tokens_per_success'])
            self.assertEqual(group['total_tokens'],4500)
            self.assertEqual(group['mean_generation_seconds'],1.5)

    def test_missing_or_duplicate_pairs_cannot_form_final_results(self):
        ids,rows=examples()
        with self.assertRaises(ValueError):report.aggregate(rows[:-1],ids)
        with self.assertRaises(ValueError):report.aggregate(rows[:-1]+[copy.deepcopy(rows[0])],ids)
        rows[0].update(success=True,judge_status='unknown')
        with self.assertRaises(ValueError):report.aggregate(rows,ids)

    def test_evidence_change_or_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'item';p.write_text('audited')
            bindings={'item':hashlib.sha256(p.read_bytes()).hexdigest()};report.check_bindings(root,bindings)
            p.write_text('changed')
            with self.assertRaises(ValueError):report.check_bindings(root,bindings)
            with self.assertRaises(ValueError):report.check_bindings(root,{'../outside':'0'*64})

    def test_running_study_cannot_emit_final_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            docs={'study.json':{'protocol_version':'common_prompt_v2','test_feedback_learned':False},
                  'completion_audit.json':{'status':'pass','completed':180,'test_feedback_learned':False,'assets_unchanged':True},
                  'report.json':{'complete':False},'state.json':{'phase':'running'},
                  'supervisor_state.json':{'phase':'generating'}}
            for name,d in docs.items():(root/name).write_text(json.dumps(d))
            with patch.object(report,'verify',return_value=None):
                with self.assertRaises(ValueError):report.load_completed(root,root/'unread_pilot.json')
            self.assertFalse((root/'metrics.json').exists())


if __name__=='__main__':unittest.main()
