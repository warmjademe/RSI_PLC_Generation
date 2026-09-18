import copy
import tempfile
from pathlib import Path
import unittest

from baseline_common.budget import Budget
from baseline_common.errors import BudgetExceeded, ModelResponseError, ProtocolError
from baseline_common.utils import content_hash
from our_method.ablation_workflow import arm_config, visible_request
from our_method.repair_policy import templates, bind, apply_action, progress, state, retrieve, learn, REPRESENTATION

BEFORE = '''FUNCTION_BLOCK F
VAR_INPUT x : INT; y : INT; END_VAR
VAR_OUTPUT z : INT; END_VAR
z := x + y;
z := z * 2;
END_FUNCTION_BLOCK'''
AFTER = BEFORE.replace('x + y', 'x - y')


def gates(*statuses):
    return [{'stage': stage, 'status': status} for stage, status in zip(('compile','runtime','formal'), statuses)]


class RepairPolicyTests(unittest.TestCase):
    def test_binding_generalizes_names_preserves_typed_roles(self):
        template, = templates(BEFORE, AFTER)
        new = BEFORE.replace('x', 'alpha').replace('y', 'beta')
        bound = bind(template, new)
        self.assertIsNotNone(bound)
        self.assertIn('alpha - beta', bound['new'])
        self.assertIsNone(bind(template, new.replace('alpha : INT', 'alpha : REAL')))
        self.assertIsNone(bind(template, new.replace('alpha : INT', 'alpha : INT := 7')))

    def test_ambiguous_match_and_scope_decline_binding(self):
        template, = templates(BEFORE, AFTER)
        self.assertIsNone(bind(template, BEFORE+'\n'+BEFORE.replace(' F', ' G')))
        sequence = 'INT; END_VAR\nz := x + y;\nz := z *'
        repeated = BEFORE.replace(sequence, sequence+'\n'+sequence)
        self.assertIsNone(bind(template, repeated))

    def test_selection_bound_to_exact_source_and_offered_action(self):
        template, = templates(BEFORE, AFTER); bound = bind(template, BEFORE)
        option = {'action_id':'p/t', 'base_code_sha256':content_hash(BEFORE),
                  'edits':[{k:bound[k] for k in ('old','new')}]}
        memory = {'items':[{'executable_options':[option]}]}
        candidate, operation = apply_action(BEFORE, {'action_id':'p/t','base_code_sha256':content_hash(BEFORE)}, memory)
        self.assertIn('x - y', candidate)
        self.assertTrue(operation['action_selected_by_model'])
        for reply in ({'action_id':'missing','base_code_sha256':content_hash(BEFORE)},
                      {'action_id':'p/t','base_code_sha256':content_hash(AFTER)},
                      {'action_id':'p/t','base_code_sha256':content_hash(BEFORE),'code':AFTER}):
            with self.assertRaises(ModelResponseError):
                apply_action(BEFORE, reply, memory)
        with self.assertRaises(ModelResponseError):
            apply_action(BEFORE, {'action_id':'p/t','base_code_sha256':content_hash(BEFORE)}, {'items':[]})

    def test_unknown_is_not_failed_progress_label(self):
        self.assertEqual(progress(gates('fail'), gates('pass','unknown'))[0], None)
        self.assertEqual(progress(gates('fail'), gates('pass','pass','pass'))[0], 1)
        self.assertEqual(progress(gates('fail'), gates('pass','fail'))[0], .5)
        self.assertEqual(progress(gates('pass','fail'), gates('fail'))[0], 0)
        self.assertEqual(state(gates('pass','unknown')), 'runtime:unknown')

    def test_twenty_attempts_require_explicit_opt_in_and_still_stop(self):
        with self.assertRaises(ProtocolError):
            Budget({'max_candidates':20})
        budget = Budget({'max_candidates':20,'max_model_calls':20}, candidate_limit=20)
        for i in range(20):
            self.assertEqual(budget.before_candidate(), i+1)
        with self.assertRaises(BudgetExceeded):
            budget.before_candidate()
        with self.assertRaises(ProtocolError):
            Budget({'max_candidates':21}, candidate_limit=20)
        self.assertEqual(Budget().limits['max_candidates'], 5)

    def test_no_assets_and_no_feedback_visibility(self):
        task={'id':'TE_1','target':'IEC_PORTABLE_ST','requirement':'add integer input values', 'metadata':{}}
        cfg={'use_code_memory':False,'use_repair_memory':False,'memory_characters':10000}
        result=retrieve([],task,gates('fail'),cfg,BEFORE)
        self.assertEqual(result['items'],[])
        self.assertFalse(result['selection_audit']['query_uses_feedback'])
        payload={'task':task,'fixed_interface_st':'','previous_code':BEFORE,'base_code_sha256':content_hash(BEFORE),
                 'memory':result,'confirmed_errors':[{'diagnostics':'private-current-diagnostic'}],
                 'task_local_attempts':{'items':[{'diagnostic':'private-history'}]},'repair_instruction':'private-repair'}
        _, visible=visible_request('NoFeedback','',payload)
        self.assertEqual(set(visible),{'task','fixed_interface_st','previous_code','base_code_sha256','memory'})

    def test_unsuccessful_episodes_enter_training_and_no_test_tasks(self):
        from baseline_common.learning.models import AttemptStep, TrajectoryEpisode
        steps=(AttemptStep(1,'','',BEFORE,None,'',tuple(gates('pass','fail')),{}),
               AttemptStep(2,'','',AFTER,None,'',tuple(gates('pass','fail')),{}))
        program={'id':'TR_1','task_id':'TR_1','metadata':{},'target':'DVP48ES300R','code':AFTER,
                 'requirement':'add integer values','candidate_sha256':content_hash(AFTER)}
        episode=TrajectoryEpisode(task_id='TR_1',run_key='run',target='DVP48ES300R',output_language='st',
            category_id='',category='',semantic_signature='',requirement=program['requirement'],interface='',
            public_metadata={},retrieval_text='',attempts=steps,success=False,terminal_status='budget_exhausted',reward=0,
            provenance={'kind':'recorded_trajectory'})
        records,summary=learn(({},[program],[],[episode]))
        revision=next(r for r in records if r['kind']=='historical_revision')
        self.assertFalse(revision['episode_terminal_success'])
        self.assertEqual(revision['progress_observation'],0)
        self.assertEqual(summary['canonical_program_records'],1)
        self.assertFalse(revision['cross_task_utility_observed'])


if __name__=='__main__':
    unittest.main()
