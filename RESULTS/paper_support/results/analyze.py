"""Recompute paper results from selected hash-verified task and call records."""
from collections import Counter, defaultdict
from pathlib import Path
import json, statistics

HERE=Path(__file__).resolve().parent
E=HERE/'evidence'
MODELS=('qwen','deepseek','haiku')
BASELINES=('Vanilla','FewShot','FinalCodeRAG','RawTrajectoryRAG','Memento','EverMemOS','MemSkill','MSCE')
ARMS=('Full','NoAssets','NoFeedback','Neither')

def read(p): return json.loads(p.read_text())
def lines(p): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def mean(x): return statistics.mean(x) if x else None
def aggregate_calls(calls):
    return dict(calls=len(calls),generation=sum(c['role']=='plc.generate' for c in calls),
        input=sum(c['input'] for c in calls),output=sum(c['output'] for c in calls),
        missing=sum(c['missing'] for c in calls),
        auxiliary_input=sum(c['input'] for c in calls if c['role']!='plc.generate'),
        auxiliary_output=sum(c['output'] for c in calls if c['role']!='plc.generate'),
        estimated=sum(c.get('estimated',0) for c in calls))

def load():
    tasks=[]
    for host in ('nas','huashuo'):
        for model in MODELS:
            root=E/host/model
            grouped=defaultdict(list)
            if host=='huashuo':
                for c in lines(root/'cost_accounting/call_costs.jsonl'):
                    known=c['usage_status']=='reported'; u=c.get('usage') or {}
                    grouped[(c['arm'],c['task_id'])].append(dict(call=c['call'],candidate=c['candidate'],
                        role=c['role'],input=u.get('input_tokens',0) if known else 0,
                        output=u.get('output_tokens',0) if known else 0,missing=int(not known),
                        estimated=sum(u.values()) if not known else 0))
            elif model!='qwen':
                for c in read(root/'cost_accounting/calls.json'):
                    u=c.get('usage') or {}
                    grouped[(c['method'],c['task_id'])].append(dict(call=c['call'],role=c['role'],
                        input=u.get('prompt_tokens',0),output=u.get('completion_tokens',0),
                        missing=int(not u and c.get('dispatched') is not False)))
            for p in sorted((root/'tests').glob('*/*/summary.json')):
                s=read(p); method=s.get('arm',s.get('method')); tid=s['task_id']
                if host=='huashuo': result=read(root/f'arms/{method}/runs/{s["order"]:04d}/result.json')
                else: result=read(p.parent/'generation/result.json')
                calls=sorted(grouped[(method,tid)],key=lambda x:x['call'])
                if host=='nas' and model=='qwen':
                    calls=[dict(call=c['call_id'],role=c['role'],input=c.get('input_tokens') or 0,
                        output=c.get('output_tokens') or 0,missing=int(not c['usage_known']),
                        estimated=(c['budget_input_tokens']+c['budget_output_tokens']) if not c['usage_known'] else 0)
                        for c in read(p.parent/'cost_calls.json')]
                gen=[c for c in calls if c['role']=='plc.generate']
                candidates=result['candidate_results']
                passing=[c['candidate_id'] for c in candidates if c['last_status_by_stage'].get('runtime')=='pass'
                         and (host=='nas' or c['last_status_by_stage'].get('compile')=='pass')]
                first=min(passing) if passing and s['success'] else None
                assert bool(first)==s['success'],(host,model,method,tid)
                assert len(calls)==s['budget']['model_calls'],(host,model,method,tid,len(calls),s['budget']['model_calls'])
                assert len(gen)==len(candidates),(host,model,method,tid,len(gen),len(candidates))
                for n,c in enumerate(gen,1):
                    assert c.get('candidate',n)==n
                    c['candidate']=n
                base_calls=calls
                if host=='huashuo' and model=='deepseek' and method=='Full':
                    # Original 10-candidate task plus its original post-task learning;
                    # continuation calls retain their original per-task memory snapshot.
                    base_calls=[c for c in calls if c['candidate']<=10]
                first10=first if first and first<=10 else None
                success_cost=aggregate_calls([c for c in base_calls if c['call']<=gen[first10-1]['call']]) if first10 else None
                row=dict(model=model,method=method,task_id=tid,order=s['order'],host=host,
                    first_success=first10,first_success_final=first,success=bool(first10),success_final=s['success'],
                    unknown=s['judge_status'] not in ('pass','passed','fail','failed'),judge_status=s['judge_status'],
                    cost=aggregate_calls(base_calls),cost_final=aggregate_calls(calls),cost_to_success=success_cost,
                    candidate_count=min(len(gen),10),candidate_count_final=len(gen),
                    not_submitted=sum(not c['submitted_to_tools'] for c in candidates[:10]),
                    candidate_statuses=[c['last_status_by_stage'] for c in candidates[:10]],
                    generation_reason=result['reason'])
                tasks.append(row)
            print('loaded',host,model,flush=True)
    return tasks

def summarize(tasks):
    groups={}
    for model in MODELS:
        groups[model]={}
        for method in (*BASELINES,*ARMS):
            rows=[t for t in tasks if t['model']==model and t['method']==method]
            assert len(rows)==100 and len({r['task_id'] for r in rows})==100
            successes=[r for r in rows if r['success']]
            totals={k:sum(r['cost'][k] for r in rows) for k in rows[0]['cost']}
            totals['tokens']=totals['input']+totals['output']
            groups[model][method]=dict(success=len(successes),first=sum(r['first_success']==1 for r in rows),
                unknown=sum(r['unknown'] for r in rows),final_success=sum(r['success_final'] for r in rows),
                mean_attempt=mean([r['first_success'] for r in successes]),
                median_attempt=statistics.median([r['first_success'] for r in successes]) if successes else None,
                mean_success_tokens=mean([r['cost_to_success']['input']+r['cost_to_success']['output'] for r in successes]),
                unknown_success_calls=sum(r['cost_to_success']['missing'] for r in successes),
                failed_task_tokens=sum(r['cost']['input']+r['cost']['output'] for r in rows if not r['success']),
                not_submitted=sum(r['not_submitted'] for r in rows),
                prefix=[sum(r['first_success'] is not None and r['first_success']<=b for r in rows) for b in range(1,11)],
                **totals)
    return groups

if __name__=='__main__':
    tasks=load(); groups=summarize(tasks)
    (HERE/'tasks.json').write_text(json.dumps(tasks,ensure_ascii=False,indent=2)+'\n')
    (HERE/'metrics.json').write_text(json.dumps(groups,ensure_ascii=False,indent=2)+'\n')
    for model,methods in groups.items():
        print('\n',model,'method S1 S10 U calls gen input output missing meanAttempt auxTokens')
        for m,v in methods.items():
            print(m,*[v[k] for k in ('first','success','unknown','calls','generation','input','output','missing','mean_attempt')],v['auxiliary_input']+v['auxiliary_output'])
