import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from baseline_common import RunContext
from baseline_common.errors import ModelResponseError, ProviderError
from baseline_common.learning.provider import LearningProvider, ModelCall
from baseline_common.providers import ReplayProvider
from baseline_MSCE.engine.induction import InductionError, validate_reflection
from baseline_MSCE.training import episode_reflection
from baseline_MemSkill.learning import MemSkillTrainer
from experiments.deepseek_study.provider import response_system
from experiments.deepseek_study.run import train_one, save
from experiments.deepseek_study.recovery import audited_budget
from test_comparison import sample_corpus


SCORES = {'correctness':0.2,'specificity':0.7,'evidence_grounding':0.8,'reusability':0.6}
REFLECTION = {'summary':'observed failure','causal_diagnosis':'unproven repair',
              'reusable_lesson':'check reset priority','boundary':['training evidence only']}


class SequenceProvider:
    kind='openai_compatible'; requested_model='fixture'; secrets=[]

    def __init__(self, replies):
        self.replies=replies; self.hints=[]

    def complete(self, role, system, payload, **kwargs):
        index=len(self.hints); self.hints.append(payload['operator_output_token_hint'])
        reply=self.replies[index]
        if isinstance(reply,Exception):raise reply
        return copy.deepcopy(reply)


def response(text, tokens, truncated=False):
    return {'text':text,'usage':{'input_tokens':10,'output_tokens':tokens},'model':'fixture',
            'finish_reason':'length' if truncated else 'stop','invalid_finish':truncated}


def context(root, provider, attempts=3):
    task={'id':'TRAIN','requirement':'learn training history','target':'IEC','interface':{},
          'files':{},'entry_file':'unused.st','editable_files':['unused.st'],'public_properties':[]}
    return RunContext(task,{'learning_response_recovery':{'max_attempts':attempts}},root,
                      method='MemSkill_training',provider=provider)


class LearningRecoveryTests(unittest.TestCase):
    def test_memskill_truncation_recovers_and_journal_counts_every_request(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); provider=SequenceProvider([
                response('{"actions":[',2600,True),response('{"actions":[]}',20)])
            ctx=context(root/'context',provider)
            trainer=object.__new__(MemSkillTrainer);trainer.provider=LearningProvider(ctx)
            arguments={'phase':'memory_executor','step':226,'messages':[{'role':'system','content':'Return JSON actions'}],
                       'max_tokens':2600}
            document,audit=trainer._journaled_json_chat(root/'journal',**arguments)
            self.assertEqual(document,{'actions':[]})
            self.assertEqual(provider.hints,[2600,4096])
            self.assertEqual(audit['usage'],{'input_tokens':20,'output_tokens':2620})
            self.assertEqual(audit['provider_request_count'],2)
            self.assertEqual(ctx.budget.model_calls,2)
            self.assertEqual(ctx.budget.candidates,0)
            self.assertTrue((ctx.output/'model/0001/error.json').exists())
            self.assertEqual(trainer._journaled_json_chat(root/'journal',**arguments),(document,audit))
            self.assertEqual(len(provider.hints),2)

    def test_persistent_truncation_stops_after_three_charged_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            provider=SequenceProvider([response('{',2600,True)]*4)
            ctx=context(Path(d)/'context',provider)
            with self.assertRaises(ModelResponseError):
                LearningProvider(ctx).json_chat([],max_tokens=2600)
            self.assertEqual(provider.hints,[2600,4096,4096])
            self.assertEqual(ctx.budget.output_tokens,7800)
            self.assertEqual(ctx.budget.model_calls,3)

    def test_learning_recovery_does_not_retry_transport_failures(self):
        with tempfile.TemporaryDirectory() as d:
            provider=SequenceProvider([ProviderError('HTTP 401')])
            ctx=context(Path(d)/'context',provider)
            with self.assertRaises(ProviderError):LearningProvider(ctx).json_chat([])
            self.assertEqual(len(provider.hints),1)

    def test_non_opted_in_context_keeps_one_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            provider=SequenceProvider([response('{',10,True)]*2)
            ctx=context(Path(d)/'context',provider,attempts=1)
            with self.assertRaises(ModelResponseError):LearningProvider(ctx).json_chat([])
            self.assertEqual(len(provider.hints),1)

    def test_flat_scores_move_without_mutating_or_inventing_values(self):
        raw={**REFLECTION,**SCORES};before=copy.deepcopy(raw)
        result=validate_reflection(raw)
        self.assertEqual(raw,before)
        self.assertEqual(result,{**REFLECTION,'scores':SCORES})
        self.assertEqual(validate_reflection(result),result)

    def test_invalid_missing_and_ambiguous_scores_are_rejected(self):
        variants=[REFLECTION,{**REFLECTION,'scores':[]},{**REFLECTION,'scores':SCORES,**SCORES}]
        for value in [True,None,'unknown',float('nan'),float('inf'),1.1,-0.1]:
            variants.append({**REFLECTION,'scores':{**SCORES,'correctness':value}})
        for value in variants:
            with self.subTest(value=value),self.assertRaises(InductionError):validate_reflection(value)

    def test_msce_journal_reuses_bound_response_and_preserves_raw_shape(self):
        class Provider:
            calls=0
            def json_chat(self,messages):
                self.calls+=1;doc={**REFLECTION,**SCORES}
                return doc,ModelCall(json.dumps(doc),'fixture','fixture',{},0,'fixture')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);provider=Provider();ep=sample_corpus()[2][0]
            first=episode_reflection(ep,root,provider)
            self.assertEqual(episode_reflection(ep,root,provider),first)
            self.assertEqual(provider.calls,1)
            receipt=json.loads(next((root/'reflection_journal').glob('*/response-*.json')).read_text())
            self.assertTrue(receipt['normalized']);self.assertNotIn('scores',receipt['raw_document'])
            changed=SimpleNamespace(task_id=ep.task_id,compact_trace=lambda:{'different':'episode'})
            with self.assertRaisesRegex(InductionError,'binding'):episode_reflection(changed,root,provider)

    def test_msce_invalid_schema_has_a_bounded_retry_without_default_scores(self):
        class Provider:
            calls=0
            def json_chat(self,messages):
                self.calls+=1
                return REFLECTION,ModelCall(json.dumps(REFLECTION),'fixture','fixture',{},0,'fixture')
        with tempfile.TemporaryDirectory() as d:
            provider=Provider();root=Path(d)
            with self.assertRaises(InductionError):episode_reflection(sample_corpus()[2][0],root,provider)
            self.assertEqual(provider.calls,3)
            self.assertEqual(len(list(root.glob('reflection_journal/*/response-*.json'))),3)
            self.assertFalse(list(root.glob('reflection_journal/*/complete.json')))

    def test_learning_does_not_receive_the_plc_program_suffix(self):
        self.assertNotIn('END_FUNCTION_BLOCK',response_system('Learn history','learning.operator'))
        self.assertEqual(response_system('Generate PLC','plc.generate'),
            'Generate PLC\nReturn one complete JSON object only, with no Markdown. Use IEC (* ... *) comments in ST and include END_FUNCTION_BLOCK.')

    def test_failed_attempt_does_not_keep_accruing_wall_time_overnight(self):
        with tempfile.TemporaryDirectory() as d:
            ctx=context(Path(d)/'attempt-1/context',ReplayProvider({'responses':[]}))
            os.utime(ctx.output/'task.json',(100,100))
            save(ctx.output.parent/'failure.json',{'epoch':150})
            with patch('experiments.deepseek_study.recovery.time.time',return_value=100000):
                self.assertEqual(audited_budget(ctx.output)['elapsed_seconds'],50)

    def test_restart_after_three_old_attempts_preserves_accounting(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);state=root/'training/MSCE'
            for i in range(1,4):
                replay=ReplayProvider({'responses':[{'role':'learning.operator','response':{},'usage':{'input_tokens':7,'output_tokens':3}}]})
                ctx=context(state/f'attempt-{i}'/'context',replay,attempts=1)
                ctx.ask('learning.operator','JSON',{})
                save(ctx.output/'closed_epoch.json',{'epoch':time.time()})
            old={str(p):p.read_bytes() for p in state.rglob('*.json')}
            save(root/'configs/MSCE.train.json',{'method':{},'provider':{}})
            module=SimpleNamespace(train=lambda *args,**kwargs:{'fixture':True})
            with patch('experiments.deepseek_study.run.load_prepared',return_value=({},[],[],[])), \
                 patch('experiments.deepseek_study.run.study_provider',return_value=ReplayProvider({'responses':[]})), \
                 patch('experiments.deepseek_study.run.seal_adapter'), \
                 patch('experiments.deepseek_study.run.importlib.import_module',return_value=module):
                train_one(root,'MSCE')
            result=json.loads((state/'attempt-4/context/training_result.json').read_text())
            self.assertEqual(result['budget']['model_calls'],3)
            self.assertEqual(result['budget']['total_tokens'],30)
            self.assertEqual(json.loads((state/'status.json').read_text())['status'],'complete')
            self.assertTrue(all(Path(p).read_bytes()==data for p,data in old.items()))


if __name__=='__main__':unittest.main()
