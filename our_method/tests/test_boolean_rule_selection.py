import copy
import unittest

from baseline_common.errors import ProtocolError
from experiments.rsi_study.boolean_rule_replay import select_transfers
from experiments.rsi_study.boolean_rule_audit import verify_selection
from our_method.tests import test_boolean_contract_rule as fixture


class TransferSelection(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.BooleanRule(); self.fixture.setUp()
        self.sources = self.fixture.programs
        self.programs = copy.deepcopy(self.sources)
        self.repairs = {}
        for n in range(3, 10):
            p = copy.deepcopy(self.sources[0]);p.update(id=f'TR_{n}', task_id=f'TR_{n}')
            p['metadata']['contamination_group_id'] = 'paired' if n in [3,4] else f'g{n}'
            self.programs.append(p)
            self.repairs[p['task_id']] = {'before_code':self.fixture.code,
                'observed_failure':{'diagnostics':'Denied mismatch' if n <= 7 else 'unrelated output mismatch'}}
        self.protocol = {**self.fixture.protocol, 'seed':3,
                         'asset_source_ids':[p['task_id'] for p in self.programs]}

    def select(self, **kwargs):
        return select_transfers(self.programs, self.repairs, self.sources,
                                self.fixture.rule, self.protocol, **kwargs)

    def test_default_is_four_diagnostic_filtered_disjoint_groups(self):
        selected = self.select()
        self.assertEqual(len(selected),4)
        self.assertEqual(len({p['metadata']['contamination_group_id'] for p in selected}),4)
        self.assertTrue(all('Denied' in self.repairs[p['task_id']]['observed_failure']['diagnostics'] for p in selected))

    def test_expansion_includes_unmentioned_diagnostics_without_duplicate_groups(self):
        selected = self.select(limit=None, require_observed_target=False)
        self.assertEqual(len(selected),6)
        self.assertTrue({'TR_8','TR_9'} <= {p['task_id'] for p in selected})
        self.assertEqual(len({p['metadata']['contamination_group_id'] for p in selected}),6)

    def test_source_group_and_test_contamination_rejected(self):
        self.programs[0]['metadata']['contamination_group_id'] = 'paired'
        selected = self.select(limit=None, require_observed_target=False)
        self.assertFalse({'TR_3','TR_4'} & {p['task_id'] for p in selected})
        self.programs[-1]['learning_context_task_ids'] = ['TE_1']
        with self.assertRaises(ProtocolError):self.select()

    def test_invalid_limits_and_unapplicable_code_are_rejected(self):
        for limit in [0,-1,True,1.2]:
            with self.assertRaises(ProtocolError):self.select(limit=limit)
        self.repairs['TR_9']['before_code'] = 'not a function block'
        self.assertNotIn('TR_9',{p['task_id'] for p in self.select(limit=None,require_observed_target=False)})

    def test_audit_reconstructs_full_training_selection(self):
        selected = self.select(limit=None, require_observed_target=False)
        source_ids = [p['task_id'] for p in self.sources]
        ids = [p['task_id'] for p in selected]
        study = {'source_ids':source_ids,'transfer_ids':ids,'task_ids':source_ids+ids,
                 'transfer_selection':{'limit':None,'require_observed_target':False,
                   'group_disjoint':True,'selection_before_replay_outcomes':True,
                   'description':'all applicable source-disjoint training groups'}}
        verify_selection(study,self.programs,self.repairs,self.fixture.rule,self.protocol)
        for mutation in ['omit','order','policy']:
            bad = copy.deepcopy(study)
            if mutation == 'omit':
                bad['transfer_ids'].pop();bad['task_ids'].pop()
            elif mutation == 'order':
                bad['transfer_ids'].reverse();bad['task_ids'] = source_ids+bad['transfer_ids']
            else:bad['transfer_selection']['require_observed_target'] = True
            with self.assertRaises(ValueError):verify_selection(bad,self.programs,self.repairs,self.fixture.rule,self.protocol)


if __name__ == '__main__':unittest.main()
