import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ablation45_study import run, audit
from experiments.historical_assets_study.amend_three import amend_spec, excluded_cost, prepare
from our_method.ablation_workflow import ARMS


def specification():
    ids=[str(i) for i in range(45)]
    return {'arms':ARMS,'task_ids':ids,'planned':180,
        'jobs':[{'arm':a,'task_id':t} for t in ids for a in ARMS],
        'model_call_limit':5,'candidate_limit':5,'ordering_seed':4,
        'primary_paired_contrasts':[['Full','NoAssets'],['Full','NoFeedback']],
        'bootstrap_seed':4,'bootstrap_replicates':100,'interpretation':'synthetic'}


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))


class ThreeArmTests(unittest.TestCase):
    def test_amendment_preserves_order_budget_and_source(self):
        old=specification(); before=copy.deepcopy(old); new=amend_spec(old,'old')
        self.assertEqual(old,before)
        self.assertEqual(new['planned'],135)
        self.assertEqual(new['jobs'],[j for j in old['jobs'] if j['arm']!='Neither'])
        self.assertEqual(new['model_call_limit'],5)
        self.assertEqual(new['candidate_limit'],5)
        self.assertEqual(new['ordering_seed'],4)

    def test_reporting_completes_at_135_and_ignores_excluded_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); spec=amend_spec(specification(),'old'); save(root/'study.json',spec)
            for arm in ARMS:
                for tid in spec['task_ids']:
                    directory=root/'tests'/arm/tid
                    row={'success':False,'first_candidate_success':False,'judge_status':'unknown','asset_exposed_calls':0}
                    save(directory/'summary.json',row)
                    save(directory/'generation/result.json',{'budget':{'model_calls':2,'input_tokens':10,'output_tokens':20,'total_tokens':30}})
            result=run.report(root)
            self.assertTrue(result['complete']); self.assertEqual(result['completed'],135)
            self.assertEqual(sum(r['completed_generation_tokens'] for r in result['arms']),4050)
            (root/'tests/Full/0/summary.json').unlink()
            self.assertFalse(run.report(root)['complete'])

    def test_completed_run_skips_model_and_judge_without_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); save(root/'study.json',amend_spec(specification(),'old'))
            target=root/'tests/Full/0/summary.json'; save(target,{'budget':{'model_calls':5,'total_tokens':400}})
            before=target.read_bytes()
            with patch.object(run,'study_provider',side_effect=AssertionError('duplicate model call')), \
                 patch.object(run.subprocess,'run',side_effect=AssertionError('duplicate judge')):
                run.test_one(root,'Full','0')
            self.assertEqual(target.read_bytes(),before)
            with self.assertRaises(ValueError):run.test_one(root,'Neither','0')

    def test_partial_retained_run_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old'; new=Path(tmp)/'new'
            save(old/'study.json',specification()); save(old/'STOP',{'reason':'amend'})
            (old/'tests/Full/0/generation').mkdir(parents=True)
            with patch('experiments.historical_assets_study.amend_three.verify'):
                with self.assertRaisesRegex(ValueError,'in-flight'):prepare(old,new,Path(tmp)/'overlay')
            self.assertFalse(new.exists())

    def test_two_contrasts_without_invented_interaction(self):
        spec=amend_spec(specification(),'old')
        rows=[{'arm':a,'task_id':t,'success':a=='Full','budget':{'total_tokens':100}}
              for a in spec['arms'] for t in spec['task_ids']]
        result=audit.analyze(rows,spec)
        self.assertEqual(len(result['primary_contrasts']),2)
        self.assertIsNone(result['exploratory_success_interaction'])

    def test_excluded_unsettled_usage_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'tests/Neither/0'
            save(path/'generation/model/0001/request.json',{})
            save(path/'wire/00001/metadata.json',{'generation_dispatched':True})
            result=excluded_cost(root)
            self.assertEqual(result['requests_without_response'],1)
            self.assertEqual(result['dispatched_without_usage'],1)
            self.assertEqual(result['settled_provider_calls'],0)


if __name__=='__main__':unittest.main()
