"""Learn source-bound, typed ST statement abstractions without a task oracle.

The supported language is assignments and nested IF, with scalar expressions.
No calls, arrays, loops, aliases, declarations, or nested control context are
silently erased. Unsupported top-level statements remain outside the library.
An abstraction preserves entry-state reads; it is not a whole-task solution.
"""
from collections import Counter, defaultdict
import re

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash, object_hash
from .code_projection import without_comments
from .rsi_protocol import groups

ID = re.compile(r'^[A-Za-z_][A-Za-z_0-9]*$')
TOKEN = re.compile(r'[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|:=|<>|<=|>=|[();:+*/=<>-]')
TYPES = {'BOOL', 'INT', 'DINT', 'SINT', 'UINT', 'UDINT', 'USINT', 'REAL', 'LREAL'}
OPS = {'OR': 1, 'XOR': 2, 'AND': 3, '=': 4, '<>': 4, '<': 5, '>': 5,
       '<=': 5, '>=': 5, '+': 6, '-': 6, '*': 7, '/': 7, 'MOD': 7}


class Unsupported(ValueError):
    pass


def tokens(text):
    result = []; end = 0
    for m in TOKEN.finditer(text):
        if text[end:m.start()].strip():
            raise Unsupported('unsupported lexical construct')
        result.append(m.group()); end = m.end()
    if text[end:].strip():
        raise Unsupported('unsupported trailing token')
    return result


class Parser:
    def __init__(self, values):
        self.values = values; self.i = 0

    def peek(self):
        return self.values[self.i].upper() if self.i < len(self.values) else ''

    def take(self, expected=None):
        if self.i >= len(self.values): raise Unsupported('unexpected end')
        value = self.values[self.i]; self.i += 1
        if expected is not None and value.upper() != expected:
            raise Unsupported('unexpected token')
        return value

    def expr(self, minimum=0):
        raw = self.take(); value = raw.upper()
        if value == '(':
            left = self.expr(); self.take(')')
        elif value in ('NOT', '-', '+'):
            left = ['unary', value, self.expr(8)]
        elif value in ('TRUE', 'FALSE') or re.fullmatch(r'\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', raw):
            left = ['literal', value]
        elif ID.fullmatch(raw):
            left = ['var', raw.casefold()]
        else: raise Unsupported('unsupported expression')
        while self.peek() in OPS and OPS[self.peek()] >= minimum:
            op = self.take().upper()
            left = ['binary', op, left, self.expr(OPS[op] + 1)]
        return left

    def statements(self, until=()):
        result = []
        while self.peek() and self.peek() not in until:
            if self.peek() == ';': self.take(); continue
            if self.peek() == 'IF':
                self.take(); condition = self.expr(); self.take('THEN')
                branches = [[condition, self.statements(('ELSIF', 'ELSE', 'END_IF'))]]
                while self.peek() == 'ELSIF':
                    self.take(); condition = self.expr(); self.take('THEN')
                    branches.append([condition, self.statements(('ELSIF', 'ELSE', 'END_IF'))])
                otherwise = []
                if self.peek() == 'ELSE': self.take(); otherwise = self.statements(('END_IF',))
                self.take('END_IF')
                if self.peek() == ';': self.take()
                result.append(['if', branches, otherwise])
            else:
                target = self.take()
                if not ID.fullmatch(target): raise Unsupported('scalar target required')
                self.take(':='); expression = self.expr(); self.take(';')
                result.append(['assign', target.casefold(), expression])
        return result


def declarations(code):
    clean = without_comments(code)
    if clean is None or any(x in clean for x in ("'", '"', '{', '}', '[')):
        raise Unsupported('unsupported declaration or lexical construct')
    start = re.match(r'\s*FUNCTION_BLOCK\s+([A-Za-z_]\w*)\b', clean, re.I)
    if not start: raise Unsupported('one function block required')
    fields = {}; end = start.end()
    for block in re.finditer(r'\b(VAR_INPUT|VAR_OUTPUT|VAR)\b(.*?)\bEND_VAR\b', clean, re.I | re.S):
        if clean[end:block.start()].strip(): raise Unsupported('declarations must precede statements')
        for line in block[2].split(';'):
            if not line.strip(): continue
            match = re.fullmatch(r'\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:\s*([A-Za-z_]\w*)\s*(?::=\s*(TRUE|FALSE|[+-]?\d+(?:\.\d+)?))?\s*', line, re.I)
            if not match or match[2].upper() not in TYPES: raise Unsupported('unsupported type')
            for name in match[1].split(','):
                name = name.strip().casefold()
                if name in fields: raise Unsupported('duplicate declaration')
                fields[name] = {'type': match[2].upper(), 'direction': block[1].upper(),
                                'initial': (match[3] or ('FALSE' if match[2].upper() == 'BOOL' else '0')).upper()}
        end = block.end()
    body = clean[end:]
    match = re.fullmatch(r'(.*?)\bEND_FUNCTION_BLOCK\s*', body, re.I | re.S)
    if not match or not fields: raise Unsupported('ambiguous function block')
    return fields, match[1]


def render_expr(e):
    if e[0] in ('var', 'literal'): return e[1]
    if e[0] == 'unary': return '(' + e[1] + ' ' + render_expr(e[2]) + ')'
    return '(' + render_expr(e[2]) + ' ' + e[1] + ' ' + render_expr(e[3]) + ')'


def render(nodes, indent=''):
    lines = []
    for n in nodes:
        if n[0] == 'assign': lines.append(indent + n[1] + ' := ' + render_expr(n[2]) + ';')
        else:
            for i, (cond, body) in enumerate(n[1]):
                lines.append(indent + ('IF ' if i == 0 else 'ELSIF ') + render_expr(cond) + ' THEN')
                lines.append(render(body, indent + '  '))
            if n[2]: lines.extend([indent + 'ELSE', render(n[2], indent + '  ')])
            lines.append(indent + 'END_IF;')
    return '\n'.join(lines)


def variables(nodes):
    reads = []; writes = []
    def expr(e):
        if e[0] == 'var': reads.append(e[1])
        elif e[0] == 'unary': expr(e[2])
        elif e[0] == 'binary': expr(e[2]); expr(e[3])
    def walk(items):
        for n in items:
            if n[0] == 'assign': expr(n[2]); writes.append(n[1])
            else:
                for cond, body in n[1]: expr(cond); walk(body)
                walk(n[2])
    walk(nodes)
    return reads, writes


def rename(nodes, bindings):
    def expr(e):
        if e[0] == 'var': return ['var', bindings[e[1]]]
        if e[0] == 'unary': return [*e[:2], expr(e[2])]
        if e[0] == 'binary': return [*e[:2], expr(e[2]), expr(e[3])]
        return list(e)
    return [['assign', bindings[n[1]], expr(n[2])] if n[0] == 'assign' else
            ['if', [[expr(c), rename(b, bindings)] for c, b in n[1]], rename(n[2], bindings)] for n in nodes]


def extract(record):
    fields, body = declarations(record['code'])
    parser = Parser(tokens(body)); nodes = parser.statements()
    descriptions = {x['name'].casefold(): x.get('description', '') for side in ('inputs', 'outputs')
                    for x in record['metadata'].get('interface', {}).get(side, [])}
    result = []
    # Complete top-level sequences preserve enclosing conditions and order.
    for begin in range(len(nodes)):
        for size in (1, 2, 3):
            fragment = nodes[begin:begin+size]
            if len(fragment) != size: continue
            code = render(fragment)
            if not 35 <= len(code) <= 2200: continue
            reads, writes = variables(fragment)
            names = list(dict.fromkeys(reads + writes))
            if not writes or not 2 <= len(names) <= 14: continue
            if any(name not in fields for name in names): continue
            if any(fields[name]['direction'] == 'VAR_INPUT' for name in writes): continue
            mapping = {name: 'p'+str(i) for i, name in enumerate(names)}
            tree = rename(fragment, mapping)
            roles = [{'slot': mapping[name], **fields[name], 'reads': name in reads, 'writes': name in writes}
                     for name in names]
            signature = object_hash({'tree': tree, 'roles': roles})
            relevant = [r.get('text', '') for r in record['metadata'].get('requirements', [])
                        if any(re.search(r'\b'+re.escape(n)+r'\b', r.get('text', ''), re.I) for n in names)]
            result.append({'signature': signature, 'tree': tree, 'roles': roles,
                'source_binding': {v:k for k,v in mapping.items()},
                'role_meanings': {mapping[n]: {'name': n, 'description': descriptions.get(n, '')} for n in names},
                'source_requirements': relevant, 'offset': begin, 'statement_count': size,
                'source_fragment_sha256': content_hash(code),
                'footprint': {'read_roles': sorted({mapping[n] for n in reads}),
                              'write_roles': sorted({mapping[n] for n in writes}),
                              'entry_value_may_be_read': sorted({mapping[n] for n in reads}),
                              'order_preserved': True, 'calls': [], 'aliases_allowed': False}})
    return result


def instantiate(asset, bindings, declared):
    if set(bindings) != {r['slot'] for r in asset['roles']} or len(set(bindings.values())) != len(bindings):
        raise ProtocolError('all roles require distinct explicit bindings')
    for role in asset['roles']:
        name = bindings[role['slot']]
        if not ID.fullmatch(name) or name not in declared:
            raise ProtocolError('binding is not a declared scalar')
        actual = declared[name]
        if any(actual[k] != role[k] for k in ('type', 'direction', 'initial')):
            raise ProtocolError('binding changes type, storage role or initialization')
    return render(rename(asset['tree'], bindings))


def credited_source(record):
    # A different task identity is not a fresh model implementation when the
    # code was deterministically assembled from existing programs. Missing
    # upstream prompts also do not establish independence from prior assets.
    return record.get('origin') == 'model_generated' and not record.get('learning_context_task_ids')


def learn(programs, repairs, *, maximum=48, category_limit=8):
    if any(not r['task_id'].startswith('TR_') or r['metadata'].get('split') != 'train' for r in programs):
        raise ProtocolError('learning rejects non-training identities')
    partitions = groups(programs); group_of = {t: i for i, group in enumerate(partitions) for t in group}
    by_task = {p['task_id']: p for p in programs}
    if any(not set(p.get('learning_context_task_ids', [])) <= set(by_task) for p in programs):
        raise ProtocolError('recursive source dependencies are missing')
    occurrences = defaultdict(list); rejected = Counter(); parsed = 0
    for p in programs:
        if content_hash(p['code']) != p['candidate_sha256']: raise ProtocolError('unbound training program')
        try: fragments = extract(p)
        except Unsupported as e: rejected[str(e)] += 1; continue
        parsed += 1
        for fragment in fragments:
            occurrences[fragment['signature']].append((p, fragment))
    # Repair provenance is evidence of a changed fragment, not causal proof.
    contrasts = defaultdict(list)
    for repair in repairs:
        if repair['task_id'] not in group_of: raise ProtocolError('repair outside training corpus')
        if not all(g.get('status') == 'pass' for g in repair.get('after_feedback', [])):
            continue
        from .guarded_retrieval import locally_verified
        if not locally_verified(repair): continue
        try:
            before = {x['signature'] for x in extract({**repair, 'code': repair['before_code']})}
            after = extract({**repair, 'code': repair['after_code']})
        except Unsupported: continue
        for fragment in after:
            if fragment['signature'] not in before:
                contrasts[fragment['signature']].append({'task_id': repair['task_id'], 'repair_id': repair['id'],
                    'before_sha256': content_hash(repair['before_code']), 'after_sha256': content_hash(repair['after_code']),
                    'trigger_stages': repair['trigger_stages'], 'causal_local_intervention': False})
    candidates = []
    for signature, pairs in occurrences.items():
        unique = {}
        for p, f in pairs:
            group=group_of[p['task_id']]
            if group not in unique or (credited_source(p) and not credited_source(unique[group][0])):
                unique[group]=(p,f)
        credited = {g for g, (p, _) in unique.items() if credited_source(p)}
        if len(credited) < 2: continue
        sources = sorted(unique.values(), key=lambda x: x[0]['task_id'])
        p, f = next((p,f) for p,f in sources if credited_source(p))
        evidence = []
        for q, g in sources:
            fields, _ = declarations(q['code'])
            concrete = instantiate(f, g['source_binding'], fields)
            assert content_hash(concrete) == g['source_fragment_sha256']
            evidence.append({'task_id': q['task_id'], 'group': group_of[q['task_id']],
                'candidate_sha256': q['candidate_sha256'], 'record_sha256': object_hash(q),
                'binding': g['source_binding'], 'fragment_sha256': g['source_fragment_sha256'],
                'verification_scope': q['verification_scope'], 'role_meanings': g['role_meanings']})
            evidence[-1].update(origin=q.get('origin','unknown'),
                credited_model_generated_group=credited_source(q),
                independent_of_parent_library=False if q.get('learning_context_task_ids') or q.get('origin')!='model_generated' else None,
                parent_context_evidence='recorded' if 'learning_context_task_ids' in q else 'not_available_in_prepared_record')
        lineage = sorted({t for q, _ in sources for t in [q['task_id'], *q.get('learning_context_task_ids', [])]})
        item = {'id': 'learned-mechanism:'+signature[:24], 'kind': 'learned_program_mechanism',
                'status': 'model_generated_task_group_occurrence_supported', 'task_id': p['task_id'],
                'source_task_ids': lineage, 'target': p['target'],
                'source_identities': {key: sorted({by_task[t]['metadata'][key] for t in lineage if by_task[t]['metadata'].get(key)})
                                      for key in ('contamination_group_id', 'semantic_signature')},
                'output_language': 'st', 'metadata': p['metadata'],
                **{k:f[k] for k in ('signature', 'tree', 'roles', 'footprint')},
                'operation_st': render(f['tree']), 'role_meanings': f['role_meanings'],
                'requirements': f['source_requirements'], 'evidence': evidence,
                'contrasts': contrasts[signature][:4], 'source_group_count': len(credited),
                'source_group_count_scope':'model_generated_task_groups_without_known_parent_context; upstream independence unverified',
                'source_policy_version':'origin_credit_v2',
                'all_observed_task_groups':len(sources),'upstream_lineage_complete':False,
                'guard': {'types_and_initialization': 'exact', 'aliasing': 'forbidden',
                          'entry_state': 'caller must establish each role value at this sequence entry',
                          'surrounding_contract': 'source obligations are context, not universal preconditions',
                          'whole_task_correctness': 'unknown until independent current-task checks'},
                'transfer_generation_verified': False, 'learning_model_calls': 0}
        # Diverse learning output; selection uses training evidence only.
        item['selection_score'] = min(len(credited), 12) + 3*bool(item['contrasts'])
        candidates.append(item)
    candidates.sort(key=lambda a: (-a['selection_score'], a['signature']))
    selected = []; category_count = Counter()
    for item in candidates:
        category = item['metadata'].get('category_id', '')
        if category_limit is not None and category_count[category] >= category_limit: continue
        selected.append(item); category_count[category] += 1
        if len(selected) >= maximum: break
    graph = {'nodes': [{'id': a['id'], 'type': 'ProgramAbstraction'} for a in selected], 'edges': []}
    evidence_nodes=set()
    for a in selected:
        for e in a['evidence']:
            if e['task_id'] not in evidence_nodes:
                graph['nodes'].append({'id':e['task_id'],'type':'TrainingEvidence'})
                evidence_nodes.add(e['task_id'])
            graph['edges'].append({'from': a['id'], 'relation': 'supported_by', 'to': e['task_id'], 'source': 'bound_training_occurrence'})
        for role in a['roles']:
            node_id=a['id']+':'+role['slot']
            graph['nodes'].append({'id':node_id,'type':'TypedRole','plc_type':role['type']})
            for relation, enabled in [('reads',role['reads']),('writes',role['writes'])]:
                if enabled:
                    graph['edges'].append({'from':a['id'],'relation':relation,'to':node_id,'type':role['type']})
    return selected, {'training_programs': len(programs), 'parsed_programs': parsed,
        'excluded_programs': dict(rejected), 'candidate_patterns': len(candidates),
        'selected_patterns': len(selected), 'selected_with_contrast': sum(bool(a['contrasts']) for a in selected),
        'categories': dict(category_count), 'learning_model_calls': 0, 'test_split_accessed': False,
        'source_origin_counts':dict(Counter(p.get('origin','unknown') for p in programs)),
        'uncredited_source_programs':sum(not credited_source(p) for p in programs),
        'upstream_conditioning_independence_established':False,
        'source_policy_version':'origin_credit_v2',
        'scope': 'typed alpha-abstraction with distinct model-generated task-group support; upstream conditioning unverified; generation utility requires experiments'}, graph
