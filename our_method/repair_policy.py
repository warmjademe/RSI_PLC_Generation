"""Offline state-conditioned revision policy and source-derived edit templates.

All outcomes are observations of adjacent TRAINING attempts. Neither terminal
success nor a positive progress score establishes a patch's causal effect or
cross-task utility. Test-time binding proposes edits; only a budgeted model
selection can apply one, and the ordinary independent checks still decide.
"""
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import copy
import json
import re

from baseline_common.errors import ModelResponseError
from baseline_common.utils import content_hash, object_hash
from .bound_edits import apply_revision

REPRESENTATION = 'historical_revision_policy_v1'
STAGES = ('compile', 'runtime', 'formal')
ALIASES = {'compiler': 'compile', 'openplc_feedback': 'runtime', 'plcverif': 'formal'}
LEX = re.compile(r"\(\*.*?\*\)|//[^\n]*|'(?:\$[\s\S]|[^'])*'|\s+|[A-Za-z_]\w*|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|:=|<>|<=|>=|\*\*|[^\s]", re.S)
WORDS = re.compile(r'[A-Za-z][A-Za-z_]{2,}')
STOP = set('the and for with from this that when then else input inputs output outputs function block program task code value values true false shall should must result check error failed pass source expected observed candidate'.split())


def lex(code):
    """Positions refer to original source, including all unedited text."""
    values = []
    for m in LEX.finditer(code):
        token = m.group()
        if token.isspace() or token.startswith(('(*', '//')):
            continue
        values.append((token, m.start(), m.end()))
    return values


def symbols(code):
    # Only simple declared scalars are eligible for automatic role binding.
    # Other syntax remains usable as historical guidance, never erased.
    result = {}
    for block in re.finditer(r'\b(VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR)\b(.*?)\bEND_VAR\b', code, re.I | re.S):
        for part in block[2].split(';'):
            part = re.sub(r'\(\*.*?\*\)', '', part, flags=re.S)
            m = re.fullmatch(r'\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:\s*(BOOL|SINT|USINT|INT|UINT|DINT|UDINT|LINT|ULINT|REAL|LREAL|TIME|WORD|DWORD|BYTE)\s*(?::=\s*([^;]+))?\s*', part, re.I)
            if not m:
                continue
            for name in m[1].split(','):
                name = name.strip().casefold()
                signature = [block[1].upper(), m[2].upper(), (m[3] or '<implicit>').strip().upper()]
                if name in result:
                    # Multi-POU scope cannot be safely inferred from a flat map.
                    result[name] = None
                else:
                    result[name] = signature
    return {k: v for k, v in result.items() if v is not None}


def phases(feedback):
    result = {}
    for item in feedback:
        stage = ALIASES.get(item.get('name', item.get('stage')), item.get('name', item.get('stage')))
        if stage in STAGES:
            result[stage] = item.get('status', 'unknown')
    return result


def state(feedback):
    statuses = phases(feedback)
    for stage in STAGES:
        if statuses.get(stage) == 'fail':
            return stage + ':fail'
        if statuses.get(stage) in ('unknown', 'error'):
            return stage + ':unknown'
    return 'initial' if not statuses else 'checks_passed'


def progress(before, after):
    old, new = phases(before), phases(after)
    if all(new.get(s) == 'pass' for s in STAGES):
        return 1., 'adjacent_three_stage_pass'
    # Unknown/missing results never become negative behavioral labels.
    failed = next((i for i, s in enumerate(STAGES) if new.get(s) == 'fail'), None)
    if failed is None:
        return None, 'inconclusive_or_incomplete'
    old_failed = next((i for i, s in enumerate(STAGES) if old.get(s) == 'fail'), None)
    if old_failed is not None and failed > old_failed and all(new.get(s) == 'pass' for s in STAGES[:failed]):
        return .5, 'advanced_to_later_confirmed_failure'
    return 0., 'continued_or_earlier_confirmed_failure'


def terms(text):
    return set(w.lower() for w in WORDS.findall(text)) - STOP


def features(code, requirement=''):
    upper = code.upper()
    patterns = {'branch': r'\bIF\b', 'case': r'\bCASE\b', 'loop': r'\b(FOR|WHILE|REPEAT)\b',
                'array': r'\bARRAY\b|\[', 'timer': r'\b(TON|TOF|TP|TIME)\b|T#',
                'real': r'\b(L?REAL)\b', 'integer': r'\b([USDL]*INT)\b',
                'boolean': r'\bBOOL\b', 'division': r'/|\bMOD\b',
                'retained_state': r'\bVAR\b', 'function': r'\bFUNCTION\b',
                'function_block': r'\bFUNCTION_BLOCK\b'}
    found = {name for name, pattern in patterns.items() if re.search(pattern, upper)}
    for key, pattern in {'reset': r'reset|复位', 'edge': r'edge|边沿',
                         'timing': r'timer|delay|duration|延时|计时',
                         'counting': r'count|计数', 'scan': r'scan|扫描'}.items():
        if re.search(pattern, requirement, re.I):
            found.add('requirement_' + key)
    return sorted(found)


def diagnostic_text(feedback):
    # Only actual diagnostics/evidence, not an inferred explanation.
    parts = [{k: g[k] for k in ('name', 'stage', 'status', 'summary', 'diagnostics', 'evidence') if k in g}
             for g in feedback if g.get('status') != 'pass']
    return json.dumps(parts, ensure_ascii=False, sort_keys=True)


def similarity(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a or b else 0.


def changes(before, after):
    old, new = lex(before), lex(after)
    a, b = [t[0].upper() for t in old], [t[0].upper() for t in new]
    return old, new, [op for op in SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != 'equal']


def action_family(old, new, operations):
    removed = [t[0].upper() for _, i, j, _, _ in operations for t in old[i:j]]
    added = [t[0].upper() for _, _, _, k, l in operations for t in new[k:l]]
    changed = set(removed + added)
    families = []
    if changed & {'VAR', 'VAR_INPUT', 'VAR_OUTPUT', 'END_VAR'}:
        families.append('declaration_revision')
    if changed & {'IF', 'ELSIF', 'ELSE', 'END_IF', 'CASE', 'END_CASE'}:
        families.append('branch_structure_revision')
    if changed & {'AND', 'OR', 'XOR', 'NOT', '=', '<>', '<', '>', '<=', '>='}:
        families.append('condition_revision')
    if changed & {'+', '-', '*', '/', 'MOD'}:
        families.append('arithmetic_revision')
    if changed & {':='}:
        families.append('assignment_revision')
    if changed & {'TON', 'TOF', 'TP', 'TIME'}:
        families.append('timer_revision')
    return '+'.join(families) if families else 'expression_or_identifier_revision'


def templates(before, after):
    """Generalize declared identifiers while retaining exact token context.

    This is restricted source-derived token edit learning, not AST
    anti-unification or a proof of applicability. Constants and syntax stay
    fixed; all after-side variables must bind in the before-side context.
    """
    old, new, operations = changes(before, after)
    old_fields, new_fields = symbols(before), symbols(after)
    common = {n: v for n, v in old_fields.items() if new_fields.get(n) == v}
    result = []
    for tag, i, j, k, l in operations:
        if j-i > 80 or l-k > 80:
            continue
        start, end = max(0, i-6), min(len(old), j+6)
        if end-start < 4:
            continue
        left = old[start:i]; right = old[j:end]
        old_window = left + old[i:j] + right
        new_window = left + new[k:l] + right
        bindings = {}
        def encode(tokens):
            out = []
            for token, _, _ in tokens:
                name = token.casefold()
                if name in common:
                    bindings.setdefault(name, 'v' + str(len(bindings)))
                    out.append({'var': bindings[name]})
                else:
                    out.append(token if token.startswith("'") else token.upper())
            return out
        source, target = encode(old_window), encode(new_window)
        old_vars = {x['var'] for x in source if isinstance(x, dict)}
        new_vars = {x['var'] for x in target if isinstance(x, dict)}
        if not new_vars <= old_vars or source == target:
            continue
        # Never apply an edit that touches declaration/POU structure.
        boundary = {'VAR', 'VAR_INPUT', 'VAR_OUTPUT', 'VAR_IN_OUT', 'END_VAR',
                    'FUNCTION_BLOCK', 'END_FUNCTION_BLOCK', 'FUNCTION', 'END_FUNCTION', 'PROGRAM', 'END_PROGRAM'}
        if any(t[0].upper() in boundary for t in old[i:j] + new[k:l]):
            continue
        roles = {slot: common[name] for name, slot in bindings.items()}
        item = {'before': source, 'after': target, 'roles': roles,
                'source_changed_token_span': [i, j], 'target_changed_token_span': [k, l],
                'source_binding': {slot: name for name, slot in bindings.items()},
                'boundary': 'six unchanged tokens on either side; scalar type/direction/initializer binding; new candidate requires complete validation'}
        item['template_id'] = 'template:' + object_hash({k: item[k] for k in ('before', 'after', 'roles')})[:24]
        result.append(item)
    return result


def bind(template, code):
    # Flat names cannot distinguish several scopes. Decline automatic binding.
    if len(re.findall(r'\b(?:FUNCTION_BLOCK|FUNCTION|PROGRAM)\b', code, re.I)) != 1:
        return None
    values = lex(code); fields = symbols(code); pattern = template['before']
    matches = []
    for i in range(len(values)-len(pattern)+1):
        variables = {}; occupied = set(); valid = True
        for p, token in zip(pattern, values[i:i+len(pattern)]):
            raw = token[0]; name = raw.casefold()
            if isinstance(p, dict):
                slot = p['var']
                if fields.get(name) != template['roles'][slot]:
                    valid = False; break
                if slot in variables and variables[slot].casefold() != name:
                    valid = False; break
                if slot not in variables and name in occupied:
                    valid = False; break
                variables[slot] = raw; occupied.add(name)
            elif p != (raw if raw.startswith("'") else raw.upper()):
                valid = False; break
        if not valid:
            continue
        start, end = values[i][1], values[i+len(pattern)-1][2]
        replacement = ' '.join(variables[t['var']] if isinstance(t, dict) else t for t in template['after'])
        old = code[start:end]
        if code.count(old) != 1 or old == replacement:
            continue
        matches.append({'old': old, 'new': replacement, 'bindings': variables})
        if len(matches) > 1:
            return None
    return matches[0] if len(matches) == 1 else None


def learn(corpus):
    programs = {r['task_id']: r for r in corpus[1]}
    records = []; statistics = Counter()
    for ep in corpus[3]:
        if ep.provenance.get('kind') != 'recorded_trajectory':
            continue
        if ep.task_id not in programs or not ep.task_id.startswith('TR_'):
            raise ValueError('non-training episode')
        statistics['recorded_episodes'] += 1
        for previous, following in zip(ep.attempts, ep.attempts[1:]):
            before, after = previous.candidate_st, following.candidate_st
            if not before or not after or before == after:
                statistics['missing_or_unchanged_pairs'] += 1; continue
            phase = state(previous.feedback)
            if phase in ('initial', 'checks_passed'):
                statistics['no_observed_repair_trigger'] += 1; continue
            old, new, operations = changes(before, after)
            score, outcome = progress(previous.feedback, following.feedback)
            program = programs[ep.task_id]
            identifier = 'policy:' + object_hash([ep.run_key, previous.number, following.number])[:24]
            item = {'id': identifier, 'kind': 'historical_revision', 'task_id': ep.task_id,
                    'target': ep.target, 'output_language': 'st', 'metadata': program['metadata'],
                    'requirement': ep.requirement, 'state': phase,
                    'code_features': features(before, ep.requirement),
                    'diagnostic_terms': sorted(terms(diagnostic_text(previous.feedback))),
                    'requirement_terms': sorted(terms(ep.requirement)),
                    'action_family': action_family(old, new, operations),
                    'progress_observation': score, 'outcome': outcome,
                    'before_code': before, 'after_code': after,
                    'before_feedback': list(previous.feedback), 'after_feedback': list(following.feedback),
                    'before_code_sha256': content_hash(before), 'after_code_sha256': content_hash(after),
                    'episode_terminal_success': ep.success, 'source_run_key': ep.run_key,
                    'source_attempts': [previous.number, following.number], 'provenance': ep.provenance,
                    'templates': templates(before, after),
                    'causal_effect_established': False, 'cross_task_utility_observed': False}
            records.append(item); statistics[outcome] += 1
    groups = defaultdict(list)
    for item in records:
        groups[(item['state'], item['action_family'])].append(item)
    for group in groups.values():
        by_task = defaultdict(list)
        for item in group:
            if item['progress_observation'] is not None:
                by_task[item['task_id']].append(item['progress_observation'])
        observations = [sum(v)/len(v) for v in by_task.values()]
        summary = {'source_task_count': len(by_task), 'transition_count': len(group),
                   'inconclusive_count': sum(r['progress_observation'] is None for r in group),
                   'smoothed_source_progress': (1+sum(observations))/(2+len(observations)),
                   'scope': 'per-source averaged adjacent progress, not a causal or transfer success estimate'}
        for item in group:
            item['action_statistics'] = summary
    summary = {**dict(statistics), 'training_tasks': len(programs),
                     'revision_records': len(records), 'state_action_groups': len(groups),
                     'templates': sum(len(r['templates']) for r in records),
                     'unique_templates': len({t['template_id'] for r in records for t in r['templates']}),
                     'training_model_calls': 0, 'additional_plc_executions': 0,
                     'uses_unsuccessful_episodes': True, 'test_feedback_learned': False,
                     'canonical_program_records': len(programs)}
    records += [{**r, 'kind': 'historical_program', 'code_features': features(r['code'], r['requirement']),
                 'requirement_terms': sorted(terms(r['requirement']))} for r in programs.values()]
    return records, summary


def eligible(record, task):
    if record.get('output_language', 'st') != 'st' or record.get('task_id') == task['id']:
        return False
    portable = task['target'] == 'IEC_PORTABLE_ST' and record.get('target') in ('DVP48ES300R', 'AS228T-A')
    if record.get('target') != task['target'] and not portable:
        return False
    meta = task.get('metadata', {})
    return not any(meta.get(k) and meta[k] == record.get('metadata', {}).get(k)
                   for k in ('contamination_group_id', 'semantic_signature'))


def edit_excerpt(record, maximum=2200):
    before, after = record['before_code'], record['after_code']
    old, new, ops = changes(before, after)
    result = []
    for _, i, j, k, l in ops:
        a, b = max(0, i-4), min(len(old), j+4)
        c, d = max(0, k-4), min(len(new), l+4)
        part = {'before': before[old[a][1]:old[b-1][2]] if b>a else '',
                'after': after[new[c][1]:new[d-1][2]] if d>c else ''}
        if len(json.dumps(result+[part], ensure_ascii=False)) > maximum:
            break
        result.append(part)
    return {'changes': result, 'shown': len(result), 'total': len(ops),
            'complete_revision': len(result) == len(ops)}


def retrieve(bank, task, feedback, config, code=''):
    maximum = int(config.get('memory_characters', 10000))
    result = {'kind': REPRESENTATION, 'items': [], 'selection_audit': {
        'asset_update': False, 'query_uses_feedback': bool(feedback),
        'selection': 'state-conditioned historical action progress with structural and diagnostic matching',
        'transfer_utility_established': False}}
    if not config.get('use_code_memory', True) and not config.get('use_repair_memory', True):
        result['selection_audit']['query_uses_feedback'] = False
        return result
    phase = state(feedback); req = terms(task['requirement'])
    feats = features(code or task.get('interface_st', ''), task['requirement'])
    diag = terms(diagnostic_text(feedback))
    if not code:
        programs = [r for r in bank if r.get('kind') == 'historical_program' and eligible(r, task)
                    and len(req & set(r['requirement_terms'])) >= 3
                    and similarity(req, r['requirement_terms']) >= .04]
        programs.sort(key=lambda r: (-2*similarity(req, r['requirement_terms'])
                                    -similarity(feats, r['code_features']), r['id']))
        for r in programs:
            item = {'id': r['id'], 'kind': 'historical_program_reference', 'task_id': r['task_id'],
                    'source_requirement': r['requirement'], 'code': r['code'],
                    'source_code_sha256': r['candidate_sha256'],
                    'use': 'Complete training reference; preserve its source conditions when considering any structure. Adapt only to the target public contract. This source pass does not validate the new program.',
                    'executable_options': []}
            if len(json.dumps(item, ensure_ascii=False)) <= maximum//2:
                result['items'].append(item); break
    records = [r for r in bank if r.get('kind') == 'historical_revision' and eligible(r, task)]
    ranked = []
    for r in records:
        overlap = req & set(r['requirement_terms'])
        structural = similarity(feats, r['code_features'])
        if feedback:
            if r['state'] != phase:
                continue
        elif len(overlap) < 3 or similarity(req, r['requirement_terms']) < .04:
            continue
        relevance = 2*similarity(req, r['requirement_terms']) + structural
        if feedback:
            relevance += 3*similarity(diag, r['diagnostic_terms'])
        prior = r['action_statistics']['smoothed_source_progress']
        ranked.append((relevance + .6*prior, r))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]['id']))
    bound_options = {}
    if code:
        for _, r in ranked[:32]:
            if r['progress_observation'] is None or r['progress_observation'] <= 0:
                continue
            options = [(t, bind(t, code)) for t in r['templates']]
            bound_options[r['id']] = [(t, b) for t, b in options if b is not None][:2]
        ranked.sort(key=lambda pair: (-pair[0] - bool(bound_options.get(pair[1]['id'])), pair[1]['id']))
    chosen_families = set(); sources = set(); considered = 0
    for score, r in ranked:
        if r['action_family'] in chosen_families or r['task_id'] in sources:
            continue
        # Failure-only cases affect ranking, but are not suggested as fixes.
        if r['progress_observation'] is None or r['progress_observation'] <= 0:
            continue
        considered += 1
        item = {'id': r['id'], 'kind': 'historical_revision_action', 'task_id': r['task_id'],
                'action_family': r['action_family'], 'source_requirement': r['requirement'][:1800],
                'source_requirement_excerpt': len(r['requirement']) > 1800,
                'source_state': r['state'], 'source_diagnostic_excerpt': diagnostic_text(r['before_feedback'])[:1400],
                'source_diagnostics_are_excerpt': True, 'historical_changes': edit_excerpt(r),
                'adjacent_outcome': r['outcome'], 'historical_action_statistics': r['action_statistics'],
                'source_code_hashes': [r['before_code_sha256'], r['after_code_sha256']],
                'use': 'Consider the historical change only if its source obligation matches this public contract. Explain the target condition and expected effect. Retain reset, initialization and scan-order exceptions. Historical progress does not prove target correctness.',
                'executable_options': []}
        if code:
            for template, bound in bound_options.get(r['id'], []):
                option = {'action_id': r['id']+'/'+template['template_id'],
                          'base_code_sha256': content_hash(code), 'edits': [{k: bound[k] for k in ('old', 'new')}],
                          'variable_binding': bound['bindings'], 'applicability': 'syntactic and scalar-role binding only; model must check public semantics'}
                item['executable_options'].append(option)
                if len(item['executable_options']) >= 2:
                    break
        proposed = {**result, 'items': result['items']+[item]}
        if len(json.dumps(proposed, ensure_ascii=False)) > maximum-256:
            item['executable_options'] = []
            proposed = {**result, 'items': result['items']+[item]}
        if len(json.dumps(proposed, ensure_ascii=False)) > maximum-256:
            continue
        result['items'].append(item); chosen_families.add(r['action_family']); sources.add(r['task_id'])
        if len(result['items']) >= int(config.get('policy_top_k', 2)):
            break
        if considered >= 50:
            break
    result['selection_audit'].update(matched_revision_records=len(ranked),
        offered_actions=sum(len(i['executable_options']) for i in result['items']))
    if len(json.dumps(result, ensure_ascii=False)) > maximum:
        raise ValueError('policy memory exceeds frozen context budget')
    return result


def apply_action(code, reply, memory):
    if 'action_id' not in reply:
        return apply_revision(code, reply)
    if 'code' in reply or 'edits' in reply:
        raise ModelResponseError('action_id cannot be combined with code or edits')
    if reply.get('base_code_sha256') != content_hash(code):
        raise ModelResponseError('selected action base hash does not match previous_code')
    options = [o for item in memory.get('items', []) for o in item.get('executable_options', [])
               if o['action_id'] == reply['action_id'] and o['base_code_sha256'] == content_hash(code)]
    if len(options) != 1:
        raise ModelResponseError('action_id must identify exactly one offered current-candidate action')
    candidate, operation = apply_revision(code, {'base_code_sha256': content_hash(code),
                                                'edits': copy.deepcopy(options[0]['edits'])})
    return candidate, {**operation, 'historical_action_id': reply['action_id'],
                       'action_selected_by_model': True, 'target_correctness_established': False}
