"""Paired descriptive contrasts and cluster-bootstrap intervals for fixed runs."""
from pathlib import Path
from collections import defaultdict, Counter
import json, random
from analyze import BASELINES, MODELS

HERE=Path(__file__).resolve().parent
rows=json.loads((HERE/'tasks.json').read_text())
ids=sorted({r['task_id'] for r in rows})
clusters=defaultdict(list)
for i,tid in enumerate(ids): clusters['_'.join(tid.split('_')[1:3])].append(i)
clusters=list(clusters.values())
lookup={(r['model'],r['method'],r['task_id']):r for r in rows}
spec=[]
for m in MODELS:
    for base in BASELINES: spec.append(('rq1',m,'Full-'+base,{'Full':1,base:-1}))
    for name,coeff in [
        ('history_with_feedback',{'Full':1,'NoAssets':-1}),
        ('history_without_feedback',{'NoFeedback':1,'Neither':-1}),
        ('feedback_with_history',{'Full':1,'NoFeedback':-1}),
        ('feedback_without_history',{'NoAssets':1,'Neither':-1}),
        ('interaction',{'Full':1,'NoAssets':-1,'NoFeedback':-1,'Neither':1})]:
        spec.append(('rq2',m,name,coeff))
vectors=[[sum(coef*int(lookup[(m,method,tid)]['success']) for method,coef in coeff.items())
          for tid in ids] for _,m,_,coeff in spec]
sums=[[sum(v[i] for i in group) for group in clusters] for v in vectors]
rng=random.Random(20260920)
replicates=[[] for _ in spec]
for _ in range(20000):
    selected=Counter(rng.randrange(len(clusters)) for _ in clusters)
    n=sum(len(clusters[g])*w for g,w in selected.items())
    for result,values in zip(replicates,sums): result.append(100*sum(values[g]*w for g,w in selected.items())/n)
def percentile(values,q):
    pos=(len(values)-1)*q; lower=int(pos); frac=pos-lower
    return values[lower]*(1-frac)+values[min(lower+1,len(values)-1)]*frac
out=[]
for (rq,m,name,coeff),v,samples in zip(spec,vectors,replicates):
    samples.sort(); family=24 if rq=='rq1' else 15; tail=.05/(2*family)
    row=dict(rq=rq,model=m,contrast=name,delta_pp=sum(v),
             ci95=[percentile(samples,.025),percentile(samples,.975)],
             ci95_family=[percentile(samples,tail),percentile(samples,1-tail)])
    if len(coeff)==2: row.update(gained=sum(x==1 for x in v),lost=sum(x==-1 for x in v))
    out.append(row)
v={'seed':20260920,'replicates':20000,'cluster':'ordered functional-category pair',
   'cluster_count':len(clusters),'interval':'paired cluster percentile bootstrap; Bonferroni within RQ',
   'rq1_family':24,'rq2_family':15,'contrasts':out}
(HERE/'contrasts.json').write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
for x in out:
    if x['rq']=='rq2' or x['contrast'] in ('Full-FinalCodeRAG','Full-FewShot'):
        print(x['model'],x['contrast'],x['delta_pp'],[round(y,1) for y in x['ci95_family']],x.get('gained'),x.get('lost'))
