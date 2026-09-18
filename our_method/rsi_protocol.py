"""Training-only identities and immutable experimental boundaries for RSI."""
from collections import defaultdict
from pathlib import Path
import hashlib
import json

from baseline_common.errors import ProtocolError
from baseline_common.utils import object_hash


def groups(examples):
    parent={r['task_id']:r['task_id'] for r in examples}
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]];x=parent[x]
        return x
    identities={}
    for r in examples:
        for key in ['contamination_group_id','semantic_signature']:
            value=r['metadata'].get(key)
            if not value:continue
            previous=identities.setdefault((key,value),r['task_id'])
            parent[find(r['task_id'])]=find(previous)
    result=defaultdict(list)
    for tid in parent:result[find(tid)].append(tid)
    return sorted([sorted(items) for items in result.values()])


def make_protocol(corpus, *, seed=20260911, rounds=3, practice_per_round=20, development_tasks=30):
    examples=corpus[1]
    if any(not r['task_id'].startswith('TR_') or r['metadata'].get('split')!='train' for r in examples):
        raise ProtocolError('RSI learning accepts only the frozen TR training identities')
    partitions=groups(examples)
    ordered=sorted(partitions,key=lambda ids:hashlib.sha256(f'{seed}:'.encode()+','.join(ids).encode()).hexdigest())
    development=[]
    while len(development)<development_tasks:
        if not ordered:raise ProtocolError('insufficient disjoint training groups')
        development.extend(ordered.pop(0))
    cohorts=[]
    for _ in range(rounds):
        cohort=[]
        while len(cohort)<practice_per_round:
            if not ordered:raise ProtocolError('insufficient practice groups')
            cohort.extend(ordered.pop(0))
        cohorts.append(sorted(cohort))
    train_ids=sorted(r['task_id'] for r in examples)
    doc={'schema_version':2,'seed':seed,'rounds':rounds,'training_ids':train_ids,
         'development_ids':sorted(development),'practice_cohorts':cohorts,
         'asset_source_ids':sorted(set(train_ids)-set(development)),
         'corpus_sha256':object_hash(corpus[0]),'max_candidates':5,
         'test_feedback_for_learning':False,'test_outcomes_for_promotion':False,
         'promotion':{'no_lost_successes':True,'no_more_unknowns':True,
                      'gain_requires_nonincreasing_tokens':True,'equal_success_min_token_reduction':0.08},
         'test_policy':'existing 50-task dataset is evaluation-only; freeze releases before reading test outcomes'}
    doc['protocol_sha256']=object_hash(doc)
    return doc


def assert_training_sources(ids, protocol, *, allow_development=False):
    allowed=set(protocol['training_ids'] if allow_development else protocol['asset_source_ids'])
    ids=set(ids)
    if not ids or not ids<=allowed or any(not tid.startswith('TR_') for tid in ids):
        raise ProtocolError('asset evidence is outside the authorized training partition')


def record_sources(record):
    if record.get('kind')=='verified_skill':
        return set(record.get('evidence_task_ids',[]))
    return {record.get('task_id',record.get('source_task_id'))} | set(record.get('learning_context_task_ids',[]))


def source_closure(examples, task_ids):
    selected=set(task_ids)
    return set(t for group in groups(examples) if selected.intersection(group) for t in group)


def subset_corpus(corpus, identities):
    allowed=set(identities)
    rows=[r for r in corpus[1] if r['task_id'] in allowed]
    if len(rows)!=len(allowed):raise ProtocolError('corpus subset identities are missing')
    info={**corpus[0],'task_count':len(rows),'successful_st_programs':len(rows),
          'parent_corpus_sha256':object_hash(corpus[0]),'selected_ids_sha256':object_hash(sorted(allowed))}
    return info,rows,[e for e in corpus[2] if e.task_id in allowed],[e for e in corpus[3] if e.task_id in allowed]
