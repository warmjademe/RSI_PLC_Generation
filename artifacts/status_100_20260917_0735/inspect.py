from pathlib import Path
from datetime import datetime,timezone,timedelta
from collections import Counter
import json,hashlib,time
r=Path('/home/qyb/RESEARCH/RSI_PLC_Generation/baseline8_qwen38q4_100_max10_20260916_v1')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
study=read(r/'study.json');snap={};verified=[];unknown_early=0
for m in study['methods']:
 rows=[]
 for p in (r/'tests'/m).glob('*/summary.json'):
  s=read(p);d=p.parent;v=read(d/'judge/judge.json');result=read(d/'generation/result.json')
  assert s['judge_sha256']==sha(d/'judge/judge.json') and s['generation_result_sha256']==sha(d/'generation/result.json')
  assert s['success']==v['success'] and s['judge_status']==v['status']
  assert s['generation_calls']<=10 and s['budget']['candidates']<=10
  if s['success']:
   assert hashlib.sha256(result['code'].encode()).hexdigest()==s['code_hash']==v['code_hash']
   receipts=[(x,read(x)) for x in (d/'generation/checks').glob('*/receipt.json')]
   rp,receipt=next((p,x) for p,x in receipts if x['check_id']==v['evidence']['check_id'])
   w=rp.parent/'workspace';raw=read(w/'authorized_result.json');effective=read(w/'authorized_request.json');trace=read(w/'native/trace.json')
   assert raw['status']=='pass' and effective['plan']==read(r/'dataset/evaluator'/(s['task_id']+'.json'))['runtime_plan']
   assert hashlib.sha256(effective['code'].encode()).hexdigest()==s['code_hash']
   assert sha(w/'authorized_result.json')==v['evidence']['authorized_result_sha256']
   assert sha(w/'authorized_request.json')==v['evidence']['authorized_request_sha256']
   expected=sum(len(step['assertions']) for c in effective['plan']['cases'] for step in c['steps'])
   assert trace and len(trace)==expected and all(x['matched'] for x in trace)
   verified.append({'method':m,'task_id':s['task_id'],'assertions':len(trace),'attempts':s['generation_calls'],'code_hash':s['code_hash']})
  if s['judge_status']=='unknown' and s['generation_calls']<10:unknown_early+=1
  rows.append({k:s[k] for k in ['method','task_id','order','success','judge_status','generation_calls','completed_epoch']})
 snap[m]=sorted(rows,key=lambda s:s['order'])
prefix=min(len(v) for v in snap.values());table=[]
for m,rs in snap.items():
 assert [s['task_id'] for s in rs]==study['task_ids'][:len(rs)]
 good=sum(s['success'] for s in rs);unknown=sum(s['judge_status']=='unknown' for s in rs)
 table.append({'method':m,'completed':len(rs),'success':good,'fail':sum(s['judge_status']=='fail' for s in rs),'unknown':unknown,'success_rate_percent':round(good/len(rs)*100,1),'common_prefix_success':sum(s['success'] for s in rs[:prefix]),'common_prefix_rate_percent':round(sum(s['success'] for s in rs[:prefix])/prefix*100,1),'first_attempt_success':sum(s['success'] and s['generation_calls']==1 for s in rs)})
watch=read(r/'watchdog/state.json');deep=r.parent/'baseline8_deepseekflash_100_max10_20260916_v1';total=sum(x['completed'] for x in table)
doc={'time_cst':datetime.now(timezone(timedelta(hours=8))).isoformat(),'qwen_root':str(r),'model':study['model'],'planned':800,'completed':total,'success':len(verified),'success_rate_percent':round(len(verified)/total*100,1),'rows':table,'common_prefix_tasks':prefix,'unknown_ended_before_10_attempts':unknown_early,'verified_successes':verified,'verified_assertions':sum(s['assertions'] for s in verified),'watchdog_epoch':watch['epoch'],'watchdog_phase':watch['phase'],'active_lanes':sum(bool(m.get('lane_pids')) for m in watch['methods']),'watchdog_blocked':watch.get('blocked',{}),'deepseek_queue':read(deep/'queue/state.json'),'deepseek_completed':len(list((deep/'tests').glob('*/*/summary.json'))),'records':snap}
print(json.dumps(doc,ensure_ascii=False))
