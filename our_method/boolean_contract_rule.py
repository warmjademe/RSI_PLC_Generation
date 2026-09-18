"""Narrow, source-bound Boolean rule with explicit application conditions.

Only an unconditional published equation OUT = REQUEST AND NOT READY is
supported. Guarded equations, temporal guards, aliases, early exits, and uses
of OUT inside the POU are rejected. Tool replay and transfer admission remain
separate requirements; these functions cannot release an asset.
"""
import copy
import re

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash, object_hash
from experiments.deepseek_study.native_runtime import interface
from .code_projection import without_comments
from .rsi_protocol import assert_training_sources, record_sources, source_closure

IDENTIFIER = r'[A-Za-z_][A-Za-z_0-9]*'
SHAPE = ['equal', 'output', ['and', 'request', ['not', 'ready']]]


def outer(text):
    text = text.strip()
    while text.startswith('(') and text.endswith(')'):
        depth = 0
        for i, c in enumerate(text):
            depth += (c == '(') - (c == ')')
            if depth == 0:
                break
        if i != len(text)-1:
            break
        text = text[1:-1].strip()
    return text


def equation_binding(prop):
    if not isinstance(prop, str):
        return None
    match = re.fullmatch(r'G\s*\((.*)\)', prop.strip(), re.S)
    if not match:
        return None
    equation = re.fullmatch(r'(' + IDENTIFIER + r')\s*=\s*(.+)', outer(match[1]), re.S)
    if not equation:
        return None
    rhs = re.fullmatch(r'(' + IDENTIFIER + r')\s+AND\s+(?:!\s*|NOT\s+)(' + IDENTIFIER + r')',
                       outer(equation[2]), re.I)
    if not rhs:
        return None
    roles = dict(zip(['output', 'request', 'ready'], [equation[1], rhs[1], rhs[2]]))
    if len({v.casefold() for v in roles.values()}) != 3:
        return None
    return roles


def eligible_bindings(metadata):
    """Interpret only the exact public contract fragment and declared types."""
    if metadata.get('scan', {}).get('output_observation') != 'scan_end':
        return []
    if metadata.get('scan', {}).get('input_sampling') != 'scan_start':
        return []
    fields = {}
    for side in ['inputs', 'outputs']:
        declarations = metadata.get('interface', {}).get(side, [])
        if not isinstance(declarations, list):
            return []
        for d in declarations:
            if not isinstance(d, dict) or not isinstance(d.get('name'), str):
                return []
            key = d['name'].casefold()
            if key in fields:
                return []
            fields[key] = (side, d.get('type'))
    found = []
    for prop in metadata.get('requirements', []):
        roles = equation_binding(prop.get('property'))
        if not roles:
            continue
        if any(fields.get(v.casefold(), (None, None))[1] != 'BOOL' for v in roles.values()):
            continue
        if fields[roles['output'].casefold()][0] != 'outputs' or fields[roles['request'].casefold()][0] != 'inputs':
            continue
        found.append({'roles': roles, 'property_id': prop['id'], 'property': prop['property'],
                      'property_sha256': object_hash(prop), 'contract_metadata_sha256': object_hash(metadata)})
    return found


def propose_rule(programs, protocol):
    assert_training_sources(set().union(*(record_sources(r) for r in programs)), protocol)
    if len(programs) < 2 or len({r['task_id'] for r in programs}) != len(programs):
        raise ProtocolError('rule requires distinct training programs')
    if len({r['target'] for r in programs}) != 1:
        raise ProtocolError('rule sources require one target')
    sources = []
    for p in programs:
        if p['kind'] != 'verified_program' or content_hash(p['code']) != p['candidate_sha256']:
            raise ProtocolError('rule source code is not bound to its recorded endpoint')
        if source_closure(programs, [p['task_id']]) != {p['task_id']}:
            raise ProtocolError('rule sources are not group-distinct')
        matches = eligible_bindings(p['metadata'])
        if len(matches) != 1:
            raise ProtocolError('source needs one unambiguous eligible contract equation')
        sources.append({'task_id': p['task_id'], 'record_id': p['id'], 'record_sha256': object_hash(p),
                        'candidate_sha256': p['candidate_sha256'], **matches[0]})
    record = {'kind': 'conditional_transform_candidate', 'status': 'candidate', 'shape': copy.deepcopy(SHAPE),
              'operation': 'append_pure_output_equation_at_scan_end', 'target': programs[0]['target'],
              'input_role_policy': 'reject direct assignment or output-argument binding to sampled input roles',
              'sources': sources, 'protocol_sha256': protocol['protocol_sha256'],
              'replay_verified': False, 'transfer_verified': False, 'released_for_generation': False}
    record['id'] = 'boolean-rule-candidate:' + object_hash(record)[:24]
    return record


def apply_rule(code, metadata, rule):
    """Return a testable candidate, never a passing verdict or release decision."""
    if rule.get('id') != 'boolean-rule-candidate:' + object_hash({k:v for k,v in rule.items() if k != 'id'})[:24]:
        raise ProtocolError('rule content hash mismatch')
    if rule.get('shape') != SHAPE or rule.get('operation') != 'append_pure_output_equation_at_scan_end':
        raise ProtocolError('unsupported rule operation')
    target = metadata.get('target')
    target = target.get('model') if isinstance(target, dict) else target
    if target != rule['target']:
        # Original training metadata omits target; callers must bind it explicitly.
        raise ProtocolError('target is missing or differs from the rule')
    matches = eligible_bindings(metadata)
    if len(matches) != 1:
        raise ProtocolError('application requires an unambiguous unconditional typed equation')
    binding = matches[0]; roles = binding['roles']
    clean = without_comments(code)
    if clean is None or any(c in clean for c in "'\"{}"):
        raise ProtocolError('unsupported lexical constructs')
    declarations = list(re.finditer(r'\b(FUNCTION_BLOCK|PROGRAM|FUNCTION)\s+(' + IDENTIFIER + r')', clean, re.I))
    if len(declarations) != 1 or declarations[0][1].upper() != 'FUNCTION_BLOCK':
        raise ProtocolError('one function block is required')
    if re.search(r'\b(RETURN|JMP|GOTO|VAR_EXTERNAL|VAR_IN_OUT|AT|REFERENCE|REF_TO|POINTER)\b', clean, re.I):
        raise ProtocolError('early exit or alias/external access is unsupported')
    ends = list(re.finditer(r'\bEND_FUNCTION_BLOCK\b', clean, re.I))
    if len(ends) != 1 or clean[ends[0].end():].strip():
        raise ProtocolError('ambiguous function block end')
    declared = interface(clean, declarations[0][2])
    for role, name in roles.items():
        declaration = declared.get(name.casefold())
        if not declaration or declaration['type'] != 'BOOL' or declaration['bounds'] is not None:
            raise ProtocolError('candidate role declaration differs from contract')
        expected = next('VAR_INPUT' if side == 'inputs' else 'VAR_OUTPUT'
                        for side in ['inputs', 'outputs'] for field in metadata['interface'][side]
                        if field['name'].casefold() == name.casefold())
        if declaration['direction'] != expected:
            raise ProtocolError('candidate role direction differs from contract')
    body = re.sub(r'\bVAR(?:_INPUT|_OUTPUT)?\b.*?\bEND_VAR\b', ' ', clean, flags=re.I | re.S)
    sampled_inputs = [name for name in roles.values() if declared[name.casefold()]['direction'] == 'VAR_INPUT']
    for name in sampled_inputs:
        escaped = re.escape(name)
        if re.search(r'\b'+escaped+r'\s*:=|=>\s*'+escaped+r'\b', body, re.I):
            raise ProtocolError('sampled input role assignment or output-argument binding is unsupported')
    output = roles['output']
    occurrences = list(re.finditer(r'\b' + re.escape(output) + r'\b', body, re.I))
    if not occurrences or any(not re.match(r'\s*:=', body[m.end():]) for m in occurrences):
        raise ProtocolError('output is read inside the POU or has no recognizable assignment')
    assignment = f"{output} := {roles['request']} AND NOT {roles['ready']};"
    # Existing assignments are side-effect-free writes to an unread BOOL output.
    # Keeping them avoids creating empty branches and preserves control structure.
    result = clean[:ends[0].start()].rstrip() + '\n' + assignment + '\n' + clean[ends[0].start():]
    return result, {'rule_id': rule['id'], 'roles': roles, 'binding': binding,
                    'before_sha256': content_hash(code), 'after_sha256': content_hash(result),
                    'conditions': {'unconditional_equation': True, 'scalar_bool_roles': True,
                                   'no_direct_input_role_write_or_output_binding': True,
                                   'output_not_read_within_pou': True, 'scan_end_observation': True,
                                   'single_function_block': True, 'no_alias_or_early_exit': True},
                    'tool_verification_required': True}
