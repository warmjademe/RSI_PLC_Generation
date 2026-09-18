"""Compact use of training transition assets, independent of test feedback."""
import json
import re

from baseline_common.memory import packet
from baseline_common.retrieval import rank_records
from experiments.mixed117_study.portability import scope_matches
from .mechanism_retrieval import words, retrieve as original_retrieve


STOP={'subsystem','input','output','true','false','request','value','state','process','current',
      'should','shall','must','with','when','then','else','from','that','this','each','program','scan'}


def terms(value):
    return set(re.findall(r'[a-z]{4,}|[\u3400-\u9fff]{2}',words(value).lower()))-STOP


def project(asset):
    roles=[]
    for role in asset['roles']:
        roles.append({'slot':role['slot'],'type':role['type'],'storage':role['direction'],
            'initial':role['initial'],'meaning_examples':asset['role_variants'][role['slot']][:2]})
    dependencies={slot:[] for slot in asset['transition']['exit_values']}
    for edge in asset['transition']['relations']:
        dependencies[edge['to']].append(edge['from'])
    # Source clauses remain whole. Omitted clauses are explicitly reported.
    context=[];chars=0
    for text in asset['requirements']:
        if not text or text in context:continue
        if chars+len(text)>1400:continue
        context.append(text);chars+=len(text)
        if len(context)==3:break
    return {'id':asset['id'],'kind':asset['kind'],'operation_st':asset['operation_st'],
        'roles':roles,'entry_value_dependencies':dependencies,'source_context':context,
        'source_context_total_clauses':len(asset['requirements']),
        'source_task_ids':[e['task_id'] for e in asset['evidence'] if e.get('credited_model_generated_group')][:2]
            or asset['source_task_ids'][:2],
        'source_group_count':asset['source_group_count'],
        'source_group_count_scope':asset.get('source_group_count_scope','legacy source count; origin credit unresolved')}


def retrieve(records,task,feedback,config):
    if not config.get('use_code_memory',True):
        # Keep the exact asset-disabled request from the preceding protocol.
        return original_retrieve(records,task,[],config)
    maximum=int(config.get('memory_characters',6000))
    if maximum<1500:raise ValueError('insufficient transition context budget')
    public=task['requirement']+'\n'+str(task.get('interface_st',task['interface']))
    query=terms(public);meta=task.get('metadata',{});types=set()
    for side in ('inputs','outputs','inouts'):
        fields=task['interface'].get(side,{})
        for field in (fields.values() if isinstance(fields,dict) else fields):
            value=field.get('type','') if isinstance(field,dict) else field
            types.update(re.findall(r'\b[A-Z]+\b',str(value).upper()))
    eligible=[]
    for asset in records:
        if asset.get('representation_version')!='training_transition_coverage_v2':continue
        if not scope_matches((asset['target'],'st'),(task['target'],'st')):continue
        if task['id'] in asset['source_task_ids']:continue
        if any(meta.get(k) and meta[k] in asset['source_identities'].get(k,[])
               for k in ('contamination_group_id','semantic_signature')):continue
        required={r['type'] for r in asset['roles'] if r['direction']=='VAR_INPUT'}
        if types and not required<=types:continue
        meaning=' '.join(text for values in asset['role_variants'].values() for text in values)
        # Two specific shared terms is a relevance filter, not proof of binding.
        if len(terms(meaning)&query)<2:continue
        eligible.append({'id':asset['id'],'description':words(meaning),
                         'requirement':words(' '.join(asset['requirements'])),'asset':asset})
    selected=[];covered=set()
    for match in rank_records(words(public),eligible,len(eligible)):
        asset=match['asset']
        relations={tuple((e['from'],e['to'])) for e in asset['transition']['relations']}
        # Different role namespaces alone do not establish different behavior.
        identity=(json.dumps(asset['tree'],sort_keys=True),tuple(sorted(relations)))
        if identity in covered:continue
        candidate=project(asset)
        trial=packet(selected+[candidate],maximum-900,label='training_transition_coverage_v2')
        if len(trial['items'])!=len(selected)+1:continue
        selected.append(candidate);covered.add(identity)
        if len(selected)>=int(config.get('mechanism_top_k',2)):break
    result=packet(selected,maximum-900,label='training_transition_coverage_v2')
    result['usage_conditions']=(
        'These are ordered training code sequences with typed placeholders. Resolve roles against the public contract. '
        'Entry dependencies refer to sequence entry, not necessarily scan entry. Preserve reset priority, '
        'assignment order, initialization and numeric types. Omit an asset if its behavior or entry values '
        'are not justified. Source context is not a universal precondition. Full candidate checks are still required.')
    result['selection_audit']={'sent_mechanisms':len(selected),'eligible_mechanisms':len(eligible),
        'selection_uses_current_feedback':False,'test_feedback_learned':False,'asset_update':False,
        'semantic_applicability_established_by_retrieval':False,'automatic_code_application':False}
    assert len(json.dumps(result,ensure_ascii=False))<=maximum
    return result
