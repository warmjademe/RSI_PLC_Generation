import json
import concurrent.futures
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

from baseline_common import RunContext
from baseline_common.workflow import run_loop
from experiments.deepseek_study.provider import shrink_optional
from experiments.deepseek_study.report import usage
from experiments.deepseek_study.validator import runtime_plan, check_interface
from experiments.deepseek_study.run import save, test_one
from experiments.deepseek_study.supervise import ready_work
from experiments.deepseek_study.provider import CloudDeepSeekProvider, OfficialFirstDeepSeekProvider
from experiments.deepseek_study.recovery import CheckpointedEmbedder, restore_budget
from experiments.deepseek_study.setup import cloud_provider_config
from baseline_common.utils import Redactor


class DeepSeekStudyTests(unittest.TestCase):
    def test_explicit_thinking_records_total_tokens_without_double_counting(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, DEEPSEEK_API_KEY='test-primary', TEAMROUTER_API_KEY='test-fallback'):
            root = Path(d); config = cloud_provider_config(root)
            for p in (config, config['fallback']):
                p.update(thinking_mode='enabled', reasoning_effort='high')
            bodies = []
            def request(provider, path, body=None, timeout=30):
                bodies.append(body)
                data = {'model': 'deepseek-flash', 'choices': [{'message': {'content': '{"code":"demo"}',
                        'reasoning_content': 'fixture'}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 25,
                                  'completion_tokens_details': {'reasoning_tokens': 20}}}
                return data, json.dumps(data).encode()
            with patch.object(CloudDeepSeekProvider, 'request_json', request):
                result = OfficialFirstDeepSeekProvider(config, root/'wire').complete('plc.generate', 'JSON', {}, max_tokens=40, timeout=5)
            self.assertEqual(result['usage'], {'input_tokens': 10, 'output_tokens': 25})
            self.assertEqual(result['reasoning_tokens_reported'], 20)
            self.assertTrue(result['reasoning_content_present'])
            self.assertEqual(bodies[0]['thinking'], {'type': 'enabled'})
            self.assertEqual(bodies[0]['reasoning_effort'], 'high')
            self.assertNotIn('temperature', bodies[0])
            self.assertEqual(bodies[0]['max_tokens'], 40)

    def test_reasoning_settings_reject_invalid_values_and_route_mismatch(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, DEEPSEEK_API_KEY='test-primary', TEAMROUTER_API_KEY='test-fallback'):
            root = Path(d)
            for mode, effort in [('invalid', 'high'), ('enabled', None), ('enabled', 'arbitrary'), ('disabled', 'high')]:
                config = cloud_provider_config(root); config.update(thinking_mode=mode, reasoning_effort=effort)
                with self.assertRaisesRegex(Exception, 'thinking|effort'):
                    OfficialFirstDeepSeekProvider(config, root/'wire')
            config = cloud_provider_config(root); config.update(thinking_mode='enabled', reasoning_effort='high')
            with self.assertRaisesRegex(Exception, 'settings must agree'):
                OfficialFirstDeepSeekProvider(config, root/'wire')

    def test_reasoning_only_truncation_keeps_usage_and_is_not_valid_code(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, DEEPSEEK_API_KEY='test-primary', TEAMROUTER_API_KEY='test-fallback'):
            root = Path(d); config = cloud_provider_config(root)
            for p in (config, config['fallback']):
                p.update(thinking_mode='enabled', reasoning_effort='high')
            data = {'model': 'deepseek-flash', 'choices': [{'message': {'content': '', 'reasoning_content': 'fixture'},
                    'finish_reason': 'length'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 40,
                    'completion_tokens_details': None}}
            with patch.object(CloudDeepSeekProvider, 'request_json', return_value=(data, json.dumps(data).encode())) as request:
                result = OfficialFirstDeepSeekProvider(config, root/'wire').complete('plc.generate', 'JSON', {}, max_tokens=40, timeout=5)
            self.assertEqual(request.call_count, 1)
            self.assertTrue(result['invalid_finish'])
            self.assertEqual(result['text'], '')
            self.assertEqual(result['usage'], {'input_tokens': 10, 'output_tokens': 40})
            self.assertIsNone(result['reasoning_tokens_reported'])

    def test_official_balance_failure_is_the_only_automatic_fallback(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, DEEPSEEK_API_KEY='test-primary', TEAMROUTER_API_KEY='test-fallback'):
            root = Path(d); config = cloud_provider_config(root)
            calls = []
            def request(provider, path, body=None, timeout=30):
                calls.append(provider.config['provider_name'])
                if provider.config['provider_name'] == 'deepseek_official':
                    raise urllib.error.HTTPError('https://api.deepseek.com/v1/chat/completions',402,'balance',None,io.BytesIO())
                self.assertEqual(body['thinking'],{'type':'disabled'})
                data = {'model':'deepseek-v4-flash-0731','choices':[{'message':{'content':'{"code":"demo"}'},'finish_reason':'stop'}],
                        'usage':{'prompt_tokens':10,'completion_tokens':5}}
                return data,json.dumps(data).encode()
            with patch.object(CloudDeepSeekProvider,'request_json',request):
                provider = OfficialFirstDeepSeekProvider(config, root/'wire')
                result = provider.complete('plc.generate','JSON',{},max_tokens=10,timeout=5)
                self.assertEqual(result['provider_request_count'],2)
                self.assertEqual(result['usage'],{'input_tokens':10,'output_tokens':5})
                # A later process honors the recorded balance failure and keeps old wire files.
                second = OfficialFirstDeepSeekProvider(config, root/'wire')
                self.assertEqual(second.complete('plc.generate','JSON',{},max_tokens=10,timeout=5)['provider_request_count'],1)
            self.assertEqual(calls,['deepseek_official','teamrouter','teamrouter'])
            receipts = [json.loads(p.read_text()) for p in sorted((root/'wire').glob('*/metadata.json'))]
            self.assertEqual([r['http_status'] for r in receipts],[402,200,200])

    def test_auth_rate_limit_and_server_errors_never_trigger_teamrouter(self):
        for status in [401,403,429,500,503]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as d, \
                 patch.dict(os.environ, DEEPSEEK_API_KEY='test-primary', TEAMROUTER_API_KEY='test-fallback'):
                root=Path(d);provider=OfficialFirstDeepSeekProvider(cloud_provider_config(root),root/'wire')
                error=urllib.error.HTTPError('https://api.deepseek.com/v1/chat/completions',status,'error',None,io.BytesIO())
                with patch.object(CloudDeepSeekProvider,'request_json',side_effect=error) as request:
                    with self.assertRaisesRegex(Exception, 'HTTP '+str(status)):
                        provider.complete('plc.generate','JSON',{},max_tokens=10,timeout=5)
                    self.assertEqual(request.call_count,1)
                self.assertFalse((root/'official_balance_state.json').exists())

    def test_successful_official_request_does_not_call_fallback(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, DEEPSEEK_API_KEY='test-primary', TEAMROUTER_API_KEY='test-fallback'):
            root=Path(d);provider=OfficialFirstDeepSeekProvider(cloud_provider_config(root),root/'wire')
            value={'text':'{}','model':'deepseek-flash','usage':{'input_tokens':2,'output_tokens':1}}
            with patch.object(provider.primary,'complete',return_value=value) as primary, patch.object(provider.fallback,'complete') as fallback:
                self.assertEqual(provider.complete('plc.generate','JSON',{},max_tokens=10,timeout=5),value)
                primary.assert_called_once();fallback.assert_not_called()

    def test_provider_change_does_not_reset_candidate_budget(self):
        task={'id':'demo','requirement':'echo','target':'IEC','interface':{},'files':{},
              'entry_file':'candidate.st','editable_files':['candidate.st'],'public_properties':[]}
        config={'provider':{'kind':'replay','responses':[{'role':'plc.generate','response':'{broken'}]*5}}
        with tempfile.TemporaryDirectory() as d:
            ctx=RunContext(task,config,Path(d)/'run',method='Vanilla')
            restore_budget(ctx,{'candidates':2,'model_calls':2,'input_tokens':20,'output_tokens':10})
            result=run_loop(task,ctx,{},lambda *args:{'items':[]})
            self.assertEqual(result['budget']['candidates'],5)
            self.assertEqual(result['budget']['model_calls'],5)
            self.assertEqual(ctx.provider.position,3)

    def test_memory_tuples_are_not_mistaken_for_credentials(self):
        value={'memory':{'sources':('task1','task2'),'scores':(0.1,0.2)}}
        self.assertEqual(Redactor().clean(value),value)
        self.assertNotEqual(Redactor(['private-fixture']).clean({'sources':('private-fixture',)}),{'sources':('private-fixture',)})

    def test_encoder_batches_resume_without_reencoding_or_accepting_corruption(self):
        class Encoder:
            model_name='fixture';revision='fixed'
            calls=0
            def encode(self,texts):
                self.calls += 1
                return [[float(len(text)),1.0] for text in texts]
        with tempfile.TemporaryDirectory() as d:
            encoder=Encoder();config={'batch_size':2,'revision':'fixed'}
            first=CheckpointedEmbedder(encoder,config,d)
            expected=first.encode(['abc','de','f'])
            second=CheckpointedEmbedder(encoder,config,d)
            self.assertEqual(second.encode(['abc','de','f']),expected)
            self.assertEqual(encoder.calls,2)
            p=next(p for p in Path(d).glob('*.json') if p.name!='progress.json')
            value=json.loads(p.read_text());value['vectors'][0][0]=999;p.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'corrupt'):
                second.encode(['abc','de','f'])

    def test_only_frozen_methods_are_scheduled_after_calibration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest = root/'source/test_dataset/manifest.jsonl'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"id":"task1"}\n{"id":"task2"}\n')
            save(root/'assets/Vanilla/adapter_manifest.json', {})
            self.assertEqual(ready_work(root), [])
            save(root/'calibration/summary.json', {'status':'fail'})
            self.assertEqual(ready_work(root), [])
            save(root/'calibration/summary.json', {'status':'pass'})
            self.assertEqual(ready_work(root), [('Vanilla','task1'), ('Vanilla','task2')])
            save(root/'assets/FewShot/adapter_manifest.json', {})
            save(root/'tests/Vanilla/task1/summary.json', {'success':False})
            save(root/'tests/FewShot/task2/scheduler_failure.json', {})
            self.assertEqual(ready_work(root, {('FewShot','task1')}), [('Vanilla','task2')])

    def test_direct_test_entry_rejects_missing_calibration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaisesRegex(RuntimeError, 'calibration'):
                test_one(root, 'Vanilla', 'task1')
            self.assertFalse((root/'tests/Vanilla/task1/generation').exists())

    def test_concurrent_controllers_reuse_the_same_completed_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            save(root/'calibration/summary.json', {'status':'pass'})
            save(root/'configs/Vanilla.test.json', {})
            directory = root/'tests/Vanilla/task1'
            save(directory/'generation/result.json', {'status':'passed', 'budget':{'candidates':5}})
            def slow_judge(*args):
                time.sleep(0.05)
                return {'success':True, 'status':'pass'}
            with patch('experiments.deepseek_study.run.load_public_task', return_value={}), \
                 patch('experiments.deepseek_study.run.verify_adapter'), \
                 patch('experiments.deepseek_study.run.judge', side_effect=slow_judge) as judge:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(test_one, root, 'Vanilla', 'task1') for _ in range(2)]
                    results = [future.result() for future in futures]
                self.assertEqual(results[0], results[1])
                self.assertEqual(judge.call_count, 1)
                self.assertEqual(results[0]['budget']['candidates'], 5)
                self.assertFalse((directory/'wire').exists())

    def test_all_frozen_test_scan_suites_convert_without_dropping_cases(self):
        root=Path(__file__).resolve().parents[1]/'test_dataset/evaluator/tasks'
        paths=list(root.glob('*/openplc_tests.json'))
        self.assertEqual(len(paths),50)
        for path in paths:
            suite=json.loads(path.read_text())
            all_cases=runtime_plan(suite,'all')['cases']
            feedback=runtime_plan(suite,'feedback')['cases']
            sealed=runtime_plan(suite,'sealed')['cases']
            self.assertEqual(len(all_cases),len(suite['cases']))
            self.assertEqual(len(feedback)+len(sealed),len(all_cases))
            self.assertTrue(set(c['id'] for c in feedback).isdisjoint(c['id'] for c in sealed))

    def test_interface_list_schema_checks_roles_and_extra_outputs(self):
        task={'interface':{'inputs':[{'name':'X','type':'BOOL'}],'outputs':[{'name':'Y','type':'BOOL'}]},
              'interface_st':'FUNCTION_BLOCK Echo\nVAR_INPUT\nX:BOOL;\nEND_VAR\nVAR_OUTPUT\nY:BOOL;\nEND_VAR\nEND_FUNCTION_BLOCK'}
        request={'task':task,'code':task['interface_st']}
        self.assertIsNone(check_interface(request))
        request['code']=request['code'].replace('Y:BOOL;','Y:BOOL; Extra:BOOL;')
        self.assertIsNotNone(check_interface(request))

    def test_each_repeat_is_checked_and_hidden_cases_are_separate(self):
        suite={'scan_period_ms':100,'real_absolute_tolerance':0.001,'cases':[
            {'id':'OT1','name':'A_feedback_1','fresh_instance':True,
             'steps':[{'inputs':{'X':True},'expect':{'Y':True},'repeat':3,'check':'each'}]},
            {'id':'OT2','name':'A_hidden_1','fresh_instance':True,
             'steps':[{'inputs':{'X':False},'expect':{'Y':False},'repeat':3,'check':'last_only'}]}]}
        public=runtime_plan(suite,'feedback');sealed=runtime_plan(suite,'sealed')
        self.assertEqual(len(public['cases']),1)
        self.assertEqual(len(public['cases'][0]['steps']),3)
        self.assertTrue(all(s['assertions'] and s['cycles']==1 for s in public['cases'][0]['steps']))
        self.assertEqual(sealed['cases'][0]['steps'][0]['cycles'],3)

    def test_malformed_completions_use_at_most_five_attempts(self):
        task={'id':'demo','requirement':'echo','target':'IEC','interface':{},'files':{},
              'entry_file':'candidate.st','editable_files':['candidate.st'],'public_properties':[]}
        config={'provider':{'kind':'replay','responses':[{'role':'plc.generate','response':'{broken'}]*6}}
        with tempfile.TemporaryDirectory() as d:
            ctx=RunContext(task,config,Path(d)/'run',method='Vanilla')
            result=run_loop(task,ctx,{},lambda *args:{'items':[]})
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['budget']['candidates'],5)
            self.assertEqual(result['budget']['model_calls'],5)
            self.assertEqual(result['budget']['tool_calls'],0)

    def test_context_projection_preserves_current_task_and_source(self):
        payload={'task':{'requirement':'fixed'},'previous_code':'fixed code','memory':{'items':[{'id':'first'},{'id':'second'}]}}
        reduced=shrink_optional(payload)
        self.assertEqual(reduced['task'],payload['task'])
        self.assertEqual(reduced['previous_code'],payload['previous_code'])
        self.assertEqual(len(reduced['memory']['items']),1)
        self.assertEqual(len(payload['memory']['items']),2)

    def test_missing_usage_is_not_an_observed_zero_token_call(self):
        with tempfile.TemporaryDirectory() as d:
            paths=[]
            for i,value in enumerate([
                {'generation_dispatched':True,'role':'plc.generate','provider_usage':{'prompt_tokens':10,'completion_tokens':5,'prompt_tokens_details':{'cached_tokens':7}}},
                {'generation_dispatched':True,'role':'plc.generate'},
                {'generation_dispatched':False,'role':'plc.generate'}]):
                p=Path(d)/str(i);p.write_text(json.dumps(value));paths.append(p)
            result=usage(paths)
            self.assertEqual(result['requests_dispatched'],2)
            self.assertEqual(result['requests_missing_usage'],1)
            self.assertEqual(result['reported_total_tokens'],15)
            self.assertEqual(result['cached_input_tokens'],7)


if __name__=='__main__':unittest.main()
