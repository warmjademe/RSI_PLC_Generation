"""Training-derived transition structure and coverage selection for a candidate bank.

This is a static analysis of the supported ST subset, not a PLC execution or a
semantic applicability proof. Store nodes preserve assignment type boundaries.
The learner reads original training records only; no test outcomes are accepted.
"""
from collections import Counter
import copy
import json
import math
import re

from baseline_common.utils import object_hash
from .typed_fragments import learn as learn_occurrences


class TransitionTooLarge(ValueError):
    pass


def transition(asset):
    roles={r['slot']:r for r in asset['roles']}
    initial={slot:['entry',slot] for slot in roles}
    written=set()

    def expression(node, state):
        if node[0]=='var': return copy.deepcopy(state[node[1]])
        if node[0]=='literal': return list(node)
        return [*node[:2],*[expression(child,state) for child in node[2:]]]

    def sequence(nodes, incoming):
        state=copy.deepcopy(incoming)
        for node in nodes:
            if node[0]=='assign':
                target=node[1]; written.add(target)
                state[target]=['store',roles[target]['type'],expression(node[2],state)]
            else:
                before=copy.deepcopy(state)
                alternatives=[(expression(cond,before),sequence(body,before)) for cond,body in node[1]]
                merged=sequence(node[2],before)
                for condition,branch in reversed(alternatives):
                    merged={slot:branch[slot] if branch[slot]==merged[slot]
                            else ['ite',condition,branch[slot],merged[slot]] for slot in roles}
                state=merged
            if len(json.dumps(state,separators=(',',':')))>100000:
                raise TransitionTooLarge('symbolic expression expansion exceeds the static-analysis bound')
        return state

    final=sequence(asset['tree'],initial)
    relations=[]
    for output in sorted(written):
        def entries(node):
            if node[0]=='entry': return {node[1]}
            return set().union(*(entries(n) for n in node if isinstance(n,list)))
        for source in sorted(entries(final[output])):
            relations.append({'from':source,'relation':'entry_value_influences_exit','to':output})
    return {'exit_values':{slot:final[slot] for slot in sorted(written)},
            'relations':relations,'assignment_type_boundaries_preserved':True,
            'scope':'symbolic ordered scalar assignments and IF; no numeric evaluation or current-task proof'}


def features(asset):
    """Only program structure and source metadata; no fixed task IDs or scores."""
    result=set(); operations=0; branches=0; copies=0; assignments=0
    def expr(node,parent='root'):
        nonlocal operations
        if node[0] in ('binary','unary'):
            operations+=1; result.add('operator:'+node[1]); result.add('operator-context:'+parent+':'+node[1])
            for child in node[2:]: expr(child,node[1])
    def walk(nodes):
        nonlocal branches,copies,assignments
        for node in nodes:
            if node[0]=='assign':
                assignments+=1; copies+=node[2][0]=='var'; expr(node[2],'assignment')
                if node[2][0]=='literal': result.add('assigned-literal:'+node[2][1])
            else:
                branches+=1;result.add('branch:else' if node[2] else 'branch:implicit-hold')
                result.add('branch:priority-chain' if len(node[1])>1 else 'branch:single')
                for condition,body in node[1]: expr(condition,'condition');walk(body)
                walk(node[2])
    walk(asset['tree'])
    for role in asset['roles']:
        result.add('type:'+role['type'])
        result.add('storage:'+role['direction'])
        if role['reads'] and role['writes']:result.add('state:read-and-write:'+role['type'])
    for category in asset.get('metadata',{}).get('source_categories',[]):
        result.add('source-category:'+str(category))
    result.add('category:'+str(asset.get('metadata',{}).get('category_id','')))
    # The direction of selection favors behavior, not repeated copy plumbing.
    return result,{'operations':operations,'branches':branches,'assignments':assignments,
                   'copy_only':assignments==copies and branches==0,
                   'contains_numeric_operator':bool(result & {'operator:+','operator:-','operator:*','operator:/','operator:MOD'})}


def representative_meanings(asset):
    """Keep concise role meanings from multiple source occurrences, not just one."""
    result={}
    for role in asset['roles']:
        slot=role['slot']; candidates=[]
        for evidence in asset['evidence']:
            meaning=evidence['role_meanings'][slot]
            value=meaning['description'] or meaning['name']
            value=re.sub(r'^Subsystem [AB]:\s*','',value)
            if value and value not in candidates:candidates.append(value)
        result[slot]=candidates[:3]
    return result


def select(candidates,maximum=48):
    eligible=[]; counts=Counter(); excluded=Counter()
    for asset in candidates:
        vocabulary,metrics=features(asset)
        if metrics['copy_only']:
            excluded['copy_only']+=1;continue
        # A single primitive connective is already in the model's language;
        # retain control sequences and interacting operations as candidates.
        if metrics['branches']==0 and metrics['operations']<=1:
            excluded['single_primitive_operation']+=1;continue
        item=copy.deepcopy(asset)
        item['structural_features']=sorted(vocabulary)
        item['structural_metrics']=metrics
        item['role_variants']=representative_meanings(asset)
        try: item['transition']=transition(asset)
        except TransitionTooLarge:
            excluded['symbolic_expansion_bound']+=1;continue
        item['representation_version']='training_transition_coverage_v2'
        # Large symbolic expansions are kept out of the prompt, not silently
        # converted into a truncated or allegedly executable transition.
        item['transition']['prompt_projection']='operation_st and roles; full symbolic graph stays in the bank'
        eligible.append(item);counts.update(vocabulary)
    selected=[];covered=Counter();n=len(eligible)
    while eligible and len(selected)<maximum:
        def score(asset):
            novelty=sum((1+math.log1p(n/counts[f]))/(1+covered[f]) for f in asset['structural_features'])
            operations=asset['structural_metrics']['operations']+asset['structural_metrics']['branches']
            support=math.log2(1+asset['source_group_count'])
            cost=1+len(asset['operation_st'])/1200
            return (novelty*(1+math.log1p(operations)/4)*(1+support/12)/cost,asset['signature'])
        chosen=max(eligible,key=score);eligible.remove(chosen)
        chosen['coverage_selection_score']=score(chosen)[0]
        selected.append(chosen);covered.update(chosen['structural_features'])
    summary={'candidate_patterns':len(candidates),'eligible_patterns':n,'excluded':dict(excluded),
        'selected_patterns':len(selected),'selected_copy_only':sum(a['structural_metrics']['copy_only'] for a in selected),
        'selected_numeric_patterns':sum(a['structural_metrics']['contains_numeric_operator'] for a in selected),
        'covered_structural_features':len(covered),'selection_uses_test_data':False,
        'selection_establishes_generation_utility':False}
    return selected,summary


def learn(programs,repairs,*,maximum=48):
    candidates,original,_=learn_occurrences(programs,repairs,maximum=100000,category_limit=None)
    selected,summary=select(candidates,maximum)
    graph={'nodes':[],'edges':[]}
    for asset in selected:
        graph['nodes'].append({'id':asset['id'],'type':'TrainingTransition'})
        for role in asset['roles']:
            graph['nodes'].append({'id':asset['id']+':'+role['slot'],'type':'TypedRole',
                                   'plc_type':role['type'],'storage':role['direction']})
            graph['edges'].append({'from':asset['id'],'relation':'has_role','to':asset['id']+':'+role['slot'],
                                   'evidence':'bound_training_declaration'})
        for edge in asset['transition']['relations']:
            graph['edges'].append({**edge,'from':asset['id']+':'+edge['from'],
                                  'to':asset['id']+':'+edge['to'],'evidence':'ordered_ST_static_analysis'})
    summary.update(training_programs=original['training_programs'],parsed_programs=original['parsed_programs'],
        excluded_programs=original['excluded_programs'],learning_model_calls=0,additional_plc_executions=0,
        training_partition='all_1000_no_holdout',test_split_accessed=False,
        semantic_applicability_established=False,representation_version='training_transition_coverage_v2',
        source_origin_counts=original['source_origin_counts'],uncredited_source_programs=original['uncredited_source_programs'],
        upstream_conditioning_independence_established=False,
        source_policy_version=original['source_policy_version'],
        source_library_algorithm='typed scalar occurrence abstraction, no category quota; structural coverage selection')
    summary['selected_content_sha256']=object_hash(selected)
    return selected,summary,graph
