"""Bounded current-task attempt history; never a cross-task learning asset."""
import copy
import json

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash, object_hash


def contract_hash(task):
    return object_hash({k: task[k] for k in ['id', 'requirement', 'target', 'interface']})


def validate_history(records, task):
    if not isinstance(records, list): raise ProtocolError('task history must be a list')
    for r in records:
        if r.get('task_id') != task['id'] or r.get('contract_sha256') != contract_hash(task):
            raise ProtocolError('task history belongs to another task or public contract')
        if not isinstance(r.get('code'), str) or r.get('code_hash') != content_hash(r['code']):
            raise ProtocolError('task history code hash mismatch')
        if not isinstance(r.get('feedback'), list): raise ProtocolError('invalid historical feedback')
        for f in r['feedback']:
            if f.get('code_hash') != r['code_hash'] or f.get('stage') not in ['compile', 'runtime', 'formal']:
                raise ProtocolError('historical feedback is not bound to the candidate')
            if f.get('status') not in ['pass', 'fail', 'unknown', 'error']:
                raise ProtocolError('unsupported historical check status')
    return records


def history_item(task, code, feedback, *, origin, change_summary=''):
    return {'task_id': task['id'], 'contract_sha256': contract_hash(task), 'code': code,
            'code_hash': content_hash(code), 'feedback': feedback, 'origin': origin,
            'change_summary': change_summary}


def feedback_for_code(records, task, code, current_feedback):
    """Restore missing context after invalid responses, without issuing receipts.

    Historical checks are considered only for the exact task, contract and code.
    Current checks supersede history stage by stage; every restored item remains
    visibly historical and cannot be attached to a newly checked candidate.
    """
    validate_history(records, task)
    validate_history([history_item(task, code, current_feedback, origin='current_feedback')], task)
    digest = content_hash(code)
    stages = {}
    for record in records:
        if record['code_hash'] != digest:
            continue
        for check in record['feedback']:
            restored = copy.deepcopy(check)
            restored.update(derived_context_only=True,
                            restored_from_task_history=True,
                            historical_origin=record['origin'])
            stages[check['stage']] = restored
    for check in current_feedback:
        stages[check['stage']] = copy.deepcopy(check)
    return [stages[stage] for stage in ('compile', 'runtime', 'formal') if stage in stages]


def brief(diagnostics):
    """Only project actual diagnostic fields; no inferred fault explanations."""
    projected = []
    for d in diagnostics:
        if isinstance(d, dict) and d.get('kind') == 'runtime_counterexample':
            projected.append({'kind': d['kind'], 'assertions': d.get('assertions', [])})
        elif isinstance(d, dict) and d.get('kind') == 'verification_frontend_error':
            projected.append({'kind': d['kind'], 'messages': d.get('messages', d)})
        else:
            projected.append(d)
    text = json.dumps(projected, ensure_ascii=False)
    return {'text': text[:850], 'truncated': len(text) > 850}


def history_packet(records, task, *, characters=4500, max_items=4):
    validate_history(records, task)
    unique = {}
    for r in records:
        if r['code_hash'] in unique: del unique[r['code_hash']]
        unique[r['code_hash']] = r
    rows = []
    for r in list(unique.values())[-max_items:]:
        checks = [f for f in r['feedback'] if f['status'] != 'pass']
        if not checks: continue
        rows.append({'code_hash': r['code_hash'], 'origin': r['origin'],
                     'change_summary': str(r.get('change_summary', ''))[:300],
                     'checks': [{'stage': f['stage'], 'status': f['status'],
                                 'diagnostic_excerpt': brief(f.get('diagnostics', []))} for f in checks]})
    packet = {'scope': 'this task and this public contract only; historical diagnostics are not fresh tool receipts',
              'known_versions': len(unique), 'items': rows, 'omitted_versions': 0,
              'instruction': 'Use these previous outcomes to avoid repeating rejected versions. Current full diagnostics take precedence.'}
    while True:
        packet['omitted_versions'] = len(unique)-len(rows)
        if len(json.dumps(packet, ensure_ascii=False)) <= characters: return packet
        if not rows: raise ProtocolError('history budget is smaller than the provenance header')
        rows.pop(0)
