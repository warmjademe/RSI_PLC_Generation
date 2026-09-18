"""Reversible syntactic decomposition of frozen training repair transitions.

Exact unchanged statements anchor sequence alignment. A single changed IF is
paired by branch position only when branch counts agree. This is an explicit
syntactic alignment, not a causal explanation or permission to apply an edit.
Every event still requires the complete source transition and its obligations.
"""
import copy
from difflib import SequenceMatcher

from baseline_common.utils import object_hash
from .typed_fragments import rename, variables


def decompose(before, after):
    parts = []
    matcher = SequenceMatcher(None, [object_hash(n) for n in before],
                              [object_hash(n) for n in after], autojunk=False)
    for tag, i, j, k, l in matcher.get_opcodes():
        old, new = before[i:j], after[k:l]
        if tag == 'equal':
            assert old == new, 'statement hash collision'
            part = {'kind': 'unchanged', 'nodes': old}
        elif len(old) == len(new) == 1 and old[0][0] == new[0][0] == 'assign' and old[0][1] == new[0][1]:
            part = {'kind': 'assignment_rhs', 'target': old[0][1],
                    'before': old[0][2], 'after': new[0][2]}
        elif len(old) == len(new) == 1 and old[0][0] == new[0][0] == 'if' and len(old[0][1]) == len(new[0][1]):
            part = {'kind': 'ordered_if', 'alignment': 'same_branch_ordinal_not_semantic_correspondence',
                    'branches': [{'before_guard': a[0], 'after_guard': b[0],
                                  'body': decompose(a[1], b[1])}
                                 for a, b in zip(old[0][1], new[0][1])],
                    'otherwise': decompose(old[0][2], new[0][2])}
        else:
            part = {'kind': 'statement_window', 'before': old, 'after': new}
        part.update(before_span=[i, j], after_span=[k, l])
        parts.append(part)
    return copy.deepcopy({'kind': 'sequence', 'parts': parts})


def reconstruct(plan, phase):
    if phase not in ('before', 'after'):
        raise ValueError('explicit source phase required')
    result = []
    for part in plan['parts']:
        if part['kind'] == 'unchanged': result.extend(part['nodes'])
        elif part['kind'] == 'assignment_rhs':
            result.append(['assign', part['target'], part[phase]])
        elif part['kind'] == 'statement_window': result.extend(part[phase])
        elif part['kind'] == 'ordered_if':
            result.append(['if', [[b[phase+'_guard'], reconstruct(b['body'], phase)]
                                  for b in part['branches']], reconstruct(part['otherwise'], phase)])
        else: raise ValueError('unknown decomposition part')
    return copy.deepcopy(result)


def events(plan):
    """Expose edit syntax and complete enclosing branch priority conditions.

Sequential dataflow and cyclic dependencies remain in the bound whole asset;
the event alone must never be advertised as a standalone executable patch.
"""
    found = []

    def walk(seq, old_path, new_path, old_controls, new_controls):
        for part in seq['parts']:
            old_pos = old_path+[part['before_span'][0]]
            new_pos = new_path+[part['after_span'][0]]
            common = {'before_path': old_pos, 'after_path': new_pos,
                      'before_controls': old_controls, 'after_controls': new_controls,
                      'before_span': part['before_span'], 'after_span': part['after_span'],
                      'whole_source_transition_required': True,
                      'causal_attribution_established': False}
            if part['kind'] == 'unchanged': continue
            if part['kind'] == 'assignment_rhs':
                found.append({**common, 'kind': 'assignment_rhs',
                              'before': [['assign', part['target'], part['before']]],
                              'after': [['assign', part['target'], part['after']]]})
            elif part['kind'] == 'statement_window':
                found.append({**common, 'kind': 'statement_window',
                              'before': part['before'], 'after': part['after']})
            elif part['kind'] == 'ordered_if':
                old_prior, new_prior = [], []
                for ordinal, branch in enumerate(part['branches']):
                    if branch['before_guard'] != branch['after_guard']:
                        found.append({**common, 'kind': 'branch_guard', 'branch_ordinal': ordinal,
                                      'before': branch['before_guard'], 'after': branch['after_guard'],
                                      'earlier_guards_before': copy.deepcopy(old_prior),
                                      'earlier_guards_after': copy.deepcopy(new_prior),
                                      'alignment': part['alignment']})
                    old_c = {'earlier_guards_false': copy.deepcopy(old_prior),
                             'this_guard_true': branch['before_guard']}
                    new_c = {'earlier_guards_false': copy.deepcopy(new_prior),
                             'this_guard_true': branch['after_guard']}
                    walk(branch['body'], old_pos+['branch', ordinal], new_pos+['branch', ordinal],
                         old_controls+[old_c], new_controls+[new_c])
                    old_prior.append(branch['before_guard']); new_prior.append(branch['after_guard'])
                walk(part['otherwise'], old_pos+['else'], new_pos+['else'],
                     old_controls+[{'earlier_guards_false': old_prior}],
                     new_controls+[{'earlier_guards_false': new_prior}])
            else: raise ValueError('unknown decomposition part')
    walk(plan, [], [], [], [])
    return copy.deepcopy(found)


def typed_core(event, roles):
    """Group identical edited syntax, not identical applicability conditions."""
    by_name = {r['slot']: r for r in roles}
    if event['kind'] == 'branch_guard':
        # Reuse the expression renamer without introducing a synthetic symbol.
        def reads(expr):
            if expr[0] == 'var': return [expr[1]]
            if expr[0] == 'unary': return reads(expr[2])
            if expr[0] == 'binary': return reads(expr[2])+reads(expr[3])
            return []
        names = list(dict.fromkeys(reads(event['before'])+reads(event['after'])))
    else:
        names = list(dict.fromkeys(sum((list(variables(event[p])[0])+list(variables(event[p])[1])
                                       for p in ('before', 'after')), [])))
    binding = {name: 'v'+str(i) for i, name in enumerate(names)}
    def expression(e):
        if e[0] == 'var': return ['var', binding[e[1]]]
        if e[0] == 'unary': return [*e[:2], expression(e[2])]
        if e[0] == 'binary': return [*e[:2], expression(e[2]), expression(e[3])]
        return list(e)
    core = {'kind': event['kind'],
            'roles': [{k: by_name[n][k] for k in ('type', 'direction', 'initial')} for n in names]}
    for phase in ('before', 'after'):
        core[phase] = expression(event[phase]) if event['kind'] == 'branch_guard' else rename(event[phase], binding)
    return core
