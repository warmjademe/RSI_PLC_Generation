import copy
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash
from our_method.repair_history import history_item
from our_method.seed_selection import choose_seed, passed_prefix


class SeedSelection(unittest.TestCase):
    def setUp(self):
        self.task = {'id':'fixture','requirement':'fixture','target':'fixture','interface':{}}

    def row(self, code, statuses):
        checks = [{'stage':stage,'status':status,'code_hash':content_hash(code),'diagnostics':[]}
                  for stage,status in zip(('compile','runtime','formal'), statuses)]
        return history_item(self.task,code,checks,origin='fixture-history')

    def test_compile_regression_rolls_back_without_claiming_pass(self):
        old = self.row('OLD',['pass','fail']); current = self.row('CURRENT',['fail'])
        code,feedback,decision = choose_seed([old,current],self.task,'CURRENT',current['feedback'])
        self.assertEqual(code,'OLD');self.assertEqual(passed_prefix(feedback),1)
        self.assertTrue(all(f['derived_context_only'] for f in feedback))
        self.assertTrue(decision['fresh_validation_required']);self.assertFalse(decision['new_pass_claimed'])

    def test_ties_and_unknown_do_not_imply_improvement(self):
        old = self.row('OLD',['pass','unknown']); current = self.row('CURRENT',['pass','fail'])
        code,_,decision = choose_seed([old,current],self.task,'CURRENT',current['feedback'])
        self.assertEqual(code,'CURRENT');self.assertFalse(decision['switched'])
        self.assertEqual(passed_prefix([{'stage':'runtime','status':'pass'}]),0)

    def test_latest_same_code_evidence_supersedes_earlier_pass(self):
        old = self.row('OLD',['pass','pass','unknown']); later = self.row('OLD',['fail'])
        current = self.row('CURRENT',['pass','fail'])
        code,_,_=choose_seed([old,later,current],self.task,'CURRENT',current['feedback'])
        self.assertEqual(code,'CURRENT')

    def test_foreign_contract_and_changed_code_hash_are_rejected(self):
        for mutation in ['task','contract','hash']:
            row = self.row('OLD',['pass','pass','unknown'])
            if mutation == 'task':row['task_id']='other'
            elif mutation == 'contract':row['contract_sha256']='bad'
            else:row['code']='tampered'
            with self.assertRaises(ProtocolError):choose_seed([row],self.task,'CURRENT',[])


if __name__ == '__main__':unittest.main()
