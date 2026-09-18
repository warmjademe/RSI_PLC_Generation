import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments.historical_assets_study.candidate_round import needs_iteration,wait_for_predecessor,PredecessorNotActivated,predecessor_was_not_activated
from experiments.historical_assets_study.study import generation_settings


def result():
    return {'complete':True,'completed':135,'planned':135,'arms':[
        {'arm':'Full','completed':45,'success':38,'completed_generation_tokens':1000},
        {'arm':'NoAssets','completed':45,'success':37,'completed_generation_tokens':1100},
        {'arm':'NoFeedback','completed':45,'success':30,'completed_generation_tokens':1200}]}


class CandidateRoundTests(unittest.TestCase):
    def test_incomplete_results_cannot_trigger_a_new_round(self):
        for changes in ({'complete':False},{'completed':134},{'planned':180}):
            with self.assertRaises(ValueError):needs_iteration({**result(),**changes})

    def test_no_component_increment_triggers_frozen_candidate(self):
        original=result();report=copy.deepcopy(original)
        self.assertFalse(needs_iteration(report)['run_candidate'])
        for index,reason in [(1,'assets'),(2,'feedback')]:
            changed=copy.deepcopy(original);changed['arms'][index]['success']=38
            decision=needs_iteration(changed)
            self.assertTrue(decision['run_candidate'])
            self.assertTrue(any(reason in r for r in decision['reasons']))
        self.assertEqual(original,report)

    def test_cost_regression_is_a_separate_iteration_reason(self):
        report=result();report['arms'][0]['completed_generation_tokens']=1101
        self.assertEqual(needs_iteration(report)['reasons'],['full_method_tokens_exceed_feedback_only'])

    def test_documented_origin_correction_requires_its_own_evaluation(self):
        self.assertEqual(needs_iteration(result(),source_policy_correction=True)['reasons'],
                         ['source_origin_credit_correction_requires_evaluation'])

    def test_candidate_settings_cannot_change_provider_or_budget(self):
        settings=generation_settings({'generation_settings':{'asset_representation':'training_transition_coverage_v2',
                                      'memory_characters':6000,'mechanism_top_k':2}})
        self.assertEqual(settings['memory_characters'],6000)
        repair=generation_settings({'generation_settings':{'asset_representation':'training_repair_slices_v2',
                                    'memory_characters':4500,'mechanism_top_k':1}})
        self.assertEqual(repair['asset_representation'],'training_repair_slices_v2')
        for override in ({'max_model_calls':10},{'provider':'other'},{'memory_characters':10001},
                         {'asset_representation':'arbitrary'},{'mechanism_top_k':4}):
            with self.assertRaises(ValueError):generation_settings({'generation_settings':override})

    def test_predecessor_failure_never_starts_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            control=Path(tmp)/'control';control.mkdir();previous=Path(tmp)/'old';previous.mkdir()
            process=SimpleNamespace(returncode=0,stdout='MainPID=0\nActiveState=failed\n')
            with patch('experiments.historical_assets_study.candidate_round.subprocess.run',return_value=process):
                with self.assertRaisesRegex(RuntimeError,'without completed audit'):
                    wait_for_predecessor(control,previous,'specific.service')

    def test_observation_failure_is_repolled_not_treated_as_completion(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            control=Path(tmp)/'control';control.mkdir();previous=Path(tmp)/'old';previous.mkdir()
            def stop_after_observation(_): (control/'STOP').write_text('stop before further observation')
            with patch('experiments.historical_assets_study.candidate_round.subprocess.run',
                       side_effect=subprocess.TimeoutExpired('systemctl',15)) as observer, \
                 patch('experiments.historical_assets_study.candidate_round.time.sleep',side_effect=stop_after_observation):
                with self.assertRaisesRegex(RuntimeError,'stopped before activation'):
                    wait_for_predecessor(control,previous,'specific.service')
            self.assertEqual(observer.call_count,1)
            self.assertTrue((control/'observation.json').exists())

    def test_unactivated_predecessor_stops_the_chain_without_new_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            control=Path(tmp)/'control';control.mkdir();previous=Path(tmp)/'old';previous.mkdir()
            owner=Path(tmp)/'old_control';owner.mkdir()
            (previous/'study.json').write_text(json.dumps({'learning_control':str(owner)}))
            (owner/'state.json').write_text(json.dumps({'phase':'candidate_not_activated'}))
            (owner/'activation_decision.json').write_text(json.dumps({'run_candidate':False}))
            process=SimpleNamespace(returncode=0,stdout='MainPID=0\nActiveState=inactive\n')
            with patch('experiments.historical_assets_study.candidate_round.subprocess.run',return_value=process):
                with self.assertRaises(PredecessorNotActivated):
                    wait_for_predecessor(control,previous,'specific.service')
                request=previous/'tests/Full/task/generation/model/0001/request.json'
                request.parent.mkdir(parents=True);request.write_text('{}')
                with self.assertRaisesRegex(RuntimeError,'without completed audit'):
                    wait_for_predecessor(control,previous,'specific.service')

    def make_skipped_chain(self,base):
        rounds=[]
        for i in range(3):
            root=base/f'round{i}';owner=base/f'control{i}'
            root.mkdir();owner.mkdir();spec={'learning_control':str(owner)}
            if i:spec['predecessor_round']=str(rounds[-1])
            (root/'study.json').write_text(json.dumps(spec))
            (owner/'state.json').write_text(json.dumps({'phase':('predecessor_not_activated' if i
                else 'candidate_not_activated'),'model_calls':0}))
            if not i:(owner/'activation_decision.json').write_text(json.dumps({'run_candidate':False}))
            rounds.append(root)
        return rounds

    def test_multi_level_skips_require_original_negative_activation_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);rounds=self.make_skipped_chain(base)
            self.assertTrue(predecessor_was_not_activated(rounds[-1]))
            (base/'control0/activation_decision.json').write_text(json.dumps({'run_candidate':True}))
            self.assertFalse(predecessor_was_not_activated(rounds[-1]))

    def test_any_intermediate_call_or_stop_prevents_skip_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);rounds=self.make_skipped_chain(base)
            request=rounds[1]/'tests/Full/task/generation/model/0001/request.json'
            request.parent.mkdir(parents=True);request.write_text('{}')
            self.assertFalse(predecessor_was_not_activated(rounds[-1]))
            request.unlink();(base/'control1/STOP').touch()
            self.assertFalse(predecessor_was_not_activated(rounds[-1]))

    def test_cycles_and_external_predecessors_are_not_skip_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);rounds=self.make_skipped_chain(base)
            for earlier in (rounds[-1],base.parent/'unrelated_round'):
                (rounds[1]/'study.json').write_text(json.dumps({'learning_control':str(base/'control1'),
                    'predecessor_round':str(earlier)}))
                self.assertFalse(predecessor_was_not_activated(rounds[-1]))

    def test_terminal_multilevel_skip_ends_wait_without_requesting_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);rounds=self.make_skipped_chain(base);control=base/'next';control.mkdir()
            process=SimpleNamespace(returncode=0,stdout='MainPID=0\nActiveState=inactive\n')
            with patch('experiments.historical_assets_study.candidate_round.subprocess.run',return_value=process) as observer:
                with self.assertRaises(PredecessorNotActivated):
                    wait_for_predecessor(control,rounds[-1],'specific.service')
            self.assertEqual(observer.call_count,1)


if __name__=='__main__':unittest.main()
