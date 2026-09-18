import contextlib
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from baseline_common.binding import seal_adapter
from baseline_common.errors import ProviderError, ProtocolError
from baseline_common.memory import freeze
from baseline_common.utils import canonical_json, content_hash
from experiments.repair_policy117_study.provider import MODEL, QwenProvider, model_slot
from our_method.ablation_workflow import arm_config
from our_method.policy_workflow import PolicyContext, run
from our_method.repair_policy import REPRESENTATION, learn
from our_method.validation_admission import admitted
from tests.test_comparison import sample_corpus, public_task


class QwenPolicyProtocolTests(unittest.TestCase):
    def test_validation_wait_does_not_spend_tool_timeout_or_allow_a_fifth_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); admission={'slots':4,'directory':str(root/'admission')}
            config={'validation_admission':admission,'provider':{'kind':'replay','responses':[]},
                'validators':{'compile':{'kind':'command','protocol':'json','test_only':True,'timeout_seconds':.2,
                    'command':[sys.executable,str(Path(__file__).parents[2]/'tests/fixture_validator.py'),'pass']}}}
            ctx=PolicyContext(public_task(),config,root/'run',method='OurMethod',arm='Full')
            ctx.begin_candidate(); result=[]; errors=[]
            def check():
                try:result.append(ctx.check('compile','EXAMPLE'))
                except BaseException as exc:errors.append(exc)
            with contextlib.ExitStack() as stack:
                for _ in range(4):stack.enter_context(admitted(admission,2))
                thread=threading.Thread(target=check); thread.start(); time.sleep(.35)
                self.assertEqual(ctx.budget.tool_calls,0)
                self.assertFalse(result)
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive()); self.assertFalse(errors)
            self.assertEqual(result[0]['status'],'pass')
            self.assertEqual(ctx.budget.tool_calls,1)
            receipt=json.loads((root/'run/admission/tool-0001.json').read_text())
            self.assertGreater(receipt['queue_seconds'],.3)
            self.assertFalse(receipt['queue_in_verifier_timeout'])

    def test_configured_slots_are_enforced_and_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.ExitStack() as stack:
                for _ in range(8):
                    stack.enter_context(model_slot(tmp, 1, 8))
                with self.assertRaises(ProviderError):
                    with model_slot(tmp, 0, 8):
                        self.fail('ninth request entered an eight-slot pool')
            with model_slot(tmp, 0, 8):
                pass
            with self.assertRaises(ProtocolError):
                with model_slot(tmp, 0, 0):
                    pass

    def test_transport_audits_exact_body_usage_and_context_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'POLICY_TEST_KEY':'synthetic'}):
            root=Path(tmp)
            config={'kind':'openai_compatible','model':MODEL,'allowed_resolved_models':[MODEL],
                'base_url':'http://example.invalid/v1','api_key_env':'POLICY_TEST_KEY',
                'chat_template_kwargs':{'enable_thinking':False},'context_tokens':131072,
                'model_concurrency':8,'slot_directory':str(root/'slots'),'temperature':0,
                'seed':20260914,'timeout_seconds':30}
            provider=QwenProvider(config, root/'wire'); calls=[]
            def request(endpoint, body=None, timeout=30):
                calls.append((endpoint,body))
                if endpoint=='/tokenize':
                    value={'count':4,'tokens':[1,2,3,4],'max_model_len':131072}
                else:
                    value={'model':MODEL,'choices':[{'message':{'content':'{"code":"EXAMPLE"}'},'finish_reason':'stop'}],
                           'usage':{'prompt_tokens':4,'completion_tokens':8,'total_tokens':12}}
                return value, canonical_json(value).encode()
            provider.request=request
            response=provider.complete('plc.generate','system',{'public':'task'},max_tokens=65536,timeout=30)
            self.assertEqual(response['usage'],{'input_tokens':4,'output_tokens':8})
            self.assertEqual([x[0] for x in calls],['/tokenize','/v1/chat/completions'])
            body=json.loads((root/'wire/00001/request_body.json').read_text())
            self.assertEqual(body,calls[1][1])
            self.assertEqual(json.loads((root/'wire/00001/metadata.json').read_text())['model_concurrency'],8)
            def too_large(endpoint, body=None, timeout=30):
                self.assertEqual(endpoint,'/tokenize')
                return {'count':66000,'tokens':[1]*66000,'max_model_len':131072},b'{}'
            provider.request=too_large
            with self.assertRaises(ProviderError):
                provider.complete('plc.generate','system',{},max_tokens=65536,timeout=30)
            self.assertFalse(json.loads((root/'wire/00002/metadata.json').read_text())['generation_dispatched'])

    def test_twenty_real_workflow_calls_and_feedback_ablation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); corpus=sample_corpus(); records,summary=learn(corpus)
            asset=root/'assets'
            freeze(asset,method='OurMethod',records=records,corpus=corpus[0],settings=summary)
            seal_adapter(asset,'OurMethod',corpus,{},summary)
            before=(asset/'records.jsonl.gz').read_bytes()
            for arm in ('Full','NoAssets','NoFeedback'):
                method=arm_config({'memory_root':str(asset),'asset_representation':REPRESENTATION,
                                  'response_representation':'bound_exact_edits'},arm)
                config={'method':method,'protocol_candidate_limit':20,
                    'budgets':{'max_candidates':20,'max_model_calls':20,'max_tool_calls':200,'max_total_tokens':2000000},
                    'provider':{'kind':'replay','responses':[{'role':'plc.generate','response':{
                        'code':'BROKEN','base_code_sha256':content_hash('' if i==0 else 'BROKEN')}} for i in range(20)]},
                    'validators':{s:{'kind':'command','protocol':'json','test_only':True,
                        'command':[sys.executable,str(Path(__file__).parents[2]/'tests/fixture_validator.py'),'fail']}
                        for s in ('compile','runtime','formal')}}
                ctx=PolicyContext(public_task(),config,root/arm,method='OurMethod',arm=arm)
                result=run(public_task(),ctx,method)
                self.assertEqual(result['budget']['model_calls'],20)
                self.assertEqual(result['budget']['candidates'],20)
                # Repeated known failures do not spend validation calls again.
                self.assertEqual(result['budget']['tool_calls'],1)
                requests=sorted((root/arm/'model').glob('*/request.json'))
                for path in requests:
                    payload=json.loads(path.read_text())['payload']
                    if arm=='NoFeedback':
                        self.assertEqual(set(payload),{'task','fixed_interface_st','previous_code','base_code_sha256','memory'})
                    if arm=='NoAssets':self.assertEqual(payload['memory']['items'],[])
            self.assertEqual((asset/'records.jsonl.gz').read_bytes(),before)


if __name__=='__main__':unittest.main()
