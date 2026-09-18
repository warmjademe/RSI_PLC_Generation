"""Four short deployment probes; no benchmark task or PLC tool is executed."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT/'config.json').read_text())
BASE = f"http://{CONFIG['host']}:{CONFIG['port']}"


def request(path, body=None):
    req = urllib.request.Request(BASE+path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def probe(index):
    body={'model':CONFIG['model_id'], 'messages':[{'role':'user',
        'content':f'Return exactly one JSON object with fields ok=true and index={index}. No other text.'}],
        'max_tokens':128, 'temperature':.7, 'top_p':.8, 'top_k':20, 'min_p':0,
        'presence_penalty':1.5, 'repeat_penalty':1.0, 'seed':20260915+index,
        'chat_template_kwargs':{'enable_thinking':False}, 'response_format':{'type':'json_object'},
        'stream':True, 'stream_options':{'include_usage':True}}
    rendered=request('/apply-template',{'messages':body['messages'],'add_generation_prompt':True,
        'chat_template_kwargs':body['chat_template_kwargs']})
    tokenized=request('/tokenize',{'content':rendered['prompt'],'add_special':True})
    (ROOT/f'probe_{index}_request.json').write_text(json.dumps(body,indent=2)+'\n')
    req=urllib.request.Request(BASE+'/v1/chat/completions',data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json'})
    started=time.monotonic();text='';usage=None;finish=None;done=False;events=[]
    with urllib.request.urlopen(req,timeout=120) as response:
        for raw in response:
            line=raw.decode().strip()
            if not line.startswith('data:'):continue
            value=line[5:].strip()
            if value=='[DONE]':done=True;break
            event=json.loads(value);events.append(event)
            assert event.get('model')==CONFIG['model_id']
            if event.get('usage'):usage=event['usage']
            for choice in event.get('choices',[]):
                assert not choice.get('delta',{}).get('reasoning_content')
                text+=choice.get('delta',{}).get('content') or ''
                finish=choice.get('finish_reason') or finish
    assert done and finish=='stop' and usage and usage['completion_tokens']>0
    assert json.loads(text)=={'ok':True,'index':index}
    assert usage['prompt_tokens']==len(tokenized['tokens']), 'native token count differs from actual model input'
    result={'index':index,'seconds':time.monotonic()-started,'text':text,'usage':usage,
            'native_prompt_tokens':len(tokenized['tokens']),'finished':True}
    (ROOT/f'probe_{index}_events.json').write_text(json.dumps(events)+'\n')
    return result


def main():
    assert request('/health')['status']=='ok'
    props=request('/props')
    assert props['model_alias']==CONFIG['model_id'] and props['total_slots']==4
    assert props['default_generation_settings']['n_ctx']==131072
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(probe,range(4)))
    assert not any(s['is_processing'] for s in request('/slots'))
    env={**os.environ,'LD_LIBRARY_PATH':str(ROOT/'runtime/app')+':'+str(ROOT/'runtime/cuda')}
    version=subprocess.run([CONFIG['engine'],'--version'],env=env,capture_output=True,text=True,check=True)
    report={'status':'pass','epoch':time.time(),'model':CONFIG['model_id'],
        'model_sha256':CONFIG['model_sha256'],'engine_version':version.stdout+version.stderr,
        'configuration':CONFIG,'probes':results,'model_calls':4,
        'reported_tokens':sum(r['usage']['total_tokens'] for r in results),
        'scope':'native tokenizer, JSON, nonthinking, four SSE streams and usage; no benchmark/PLC execution'}
    (ROOT/'deployment_verified.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
