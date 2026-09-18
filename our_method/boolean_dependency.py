"""Observed dependency diagnosis for a source-bound Boolean contract relation.

This builds a small typed relation graph and evaluates it against supplied
scan observations. Callers must bind the trace to the code/tool evidence.
It is not a causal proof, complete PLC semantics, or an asset release.
"""
import collections
import copy

from baseline_common.utils import content_hash,object_hash
from .boolean_contract_rule import apply_rule


def boolean(value):
    if type(value) is bool:return value
    if type(value) is int and value in (0,1):return bool(value)
    return None


def analyze(rule,metadata,code,plan,trace):
    _,binding=apply_rule(code,metadata,rule)
    roles={k:v.casefold() for k,v in binding['roles'].items()}
    directions={d['name'].casefold():side for side in ('inputs','outputs') for d in metadata['interface'][side]}
    equation='equation:'+binding['binding']['property_id']
    graph={'nodes':[{'id':'symbol:'+name,'type':'Symbol','role':role,'direction':directions[name],'iec_type':'BOOL'} for role,name in roles.items()]+
                   [{'id':equation,'type':'BooleanEquation','shape':copy.deepcopy(rule['shape'])}],
           'edges':[{'source':'symbol:'+roles[role],'relation':'read_by','target':equation} for role in ('request','ready')]+
                   [{'source':equation,'relation':'constrains','target':'symbol:'+roles['output']}]}
    grouped=collections.defaultdict(list)
    for row in trace:grouped[(row['case'],row['step'])].append(row)
    decisions=[]
    for ci,case in enumerate(plan['cases']):
        case_id=case.get('id',str(ci))
        inputs={k.casefold():v for k,v in case.get('initial_state',{}).items() if directions.get(k.casefold())=='inputs'}
        for si,step in enumerate(case['steps']):
            inputs.update({k.casefold():v for k,v in step.get('inputs',{}).items()})
            observations=grouped.get((case_id,si),[])
            target=[r for r in observations if r['variable'].casefold()==roles['output']]
            if not target:continue
            actual={};expected={};conflict=False
            for r in observations:
                name=r['variable'].casefold()
                if name not in roles.values():continue
                value=boolean(r['observed'])
                if name in actual and actual[name]!=value:conflict=True
                actual[name]=value
                if r['operator']=='eq':
                    value=boolean(r['expected'])
                    if name in expected and expected[name]!=value:conflict=True
                    expected[name]=value
            for name in roles.values():
                if directions[name]=='inputs':actual[name]=boolean(inputs.get(name))
            row={'case':case_id,'step':si,'target_assertion_failed':any(not r['matched'] for r in target),
                 'observed_values':{role:actual.get(name) for role,name in roles.items()},
                 'expected_output':expected.get(roles['output']),
                 'expected_ready':expected.get(roles['ready']),
                 'diagnosis':'insufficient_observations'}
            if not conflict and all(actual.get(name) is not None for name in roles.values()):
                rhs=actual[roles['request']] and not actual[roles['ready']]
                holds=actual[roles['output']]==rhs
                row['relation_holds_on_observed_values']=holds
                if not row['target_assertion_failed']:
                    row['diagnosis']='no_observed_target_failure'
                elif not holds:
                    row['diagnosis']='inspect_relation_implementation'
                elif (directions[roles['ready']]=='outputs' and expected.get(roles['ready']) is not None and
                      expected[roles['ready']]!=actual[roles['ready']] and expected.get(roles['output']) is not None and
                      expected[roles['output']] == (actual[roles['request']] and not expected[roles['ready']])):
                    row['diagnosis']='inspect_dependency'
                    row['suspect_dependency']='symbol:'+roles['ready']
                    row['interpretation']='the equation holds on observed values; an incorrect dependency explains this output mismatch algebraically, without proving the upstream cause'
                else:row['diagnosis']='unresolved_dependency_or_contract'
            decisions.append(row)
    return {'kind':'observed_boolean_dependency_analysis','rule_id':rule['id'],
            'code_sha256':content_hash(code),'metadata_sha256':object_hash(metadata),
            'plan_sha256':object_hash(plan),'trace_sha256':object_hash(trace),
            'graph':graph,'decisions':decisions,'model_calls':0,'released_for_generation':False,
            'scope':'supplied same-program observations only; caller must verify source receipts; not exhaustive semantics or causal proof'}
