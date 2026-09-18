"""Ephemeral query inputs; this module never writes or updates training assets."""
import json

from baseline_common.errors import ProtocolError

PUBLIC_ONLY = 'public_only'
CURRENT_FEEDBACK = 'public_and_current_feedback_v1'
STAGES = ('compile', 'runtime', 'formal')
STATUSES = ('fail', 'unknown', 'error')


def query_mode(config):
    mode = config.get('knowledge_query_mode', PUBLIC_ONLY)
    if mode not in (PUBLIC_ONLY, CURRENT_FEEDBACK):
        raise ProtocolError('unsupported knowledge query mode')
    return mode


def project_feedback(feedback):
    """Match raw receipts and visible diagnostics without code/excerpt/evidence fields.

    Keep unknown/error labels intact. Response-format errors concern the model
    protocol and do not select PLC knowledge. Each stage has one current result.
    """
    if not isinstance(feedback, list):
        raise ProtocolError('knowledge query feedback must be a list')
    rows = {}
    for item in feedback:
        if not isinstance(item, dict):
            raise ProtocolError('invalid knowledge query feedback record')
        stage, status = item.get('stage'), item.get('status')
        if stage not in STAGES or status not in STATUSES:
            continue
        if stage in rows or not isinstance(item.get('diagnostics'), list):
            raise ProtocolError('ambiguous or malformed current diagnostic')
        rows[stage] = {'stage': stage, 'status': status, 'diagnostics': item['diagnostics']}
    # Round-trip copies values so even a caller mutating the projection cannot
    # change recorded feedback. Canonical ordering is independent of wire keys.
    return json.loads(json.dumps([rows[s] for s in STAGES if s in rows], sort_keys=True))


def active_feedback(feedback, config):
    mode = query_mode(config)
    if (mode == PUBLIC_ONLY or not config.get('use_code_memory', True)
            or not config.get('use_current_task_feedback', True)):
        return []
    return project_feedback(feedback)


def feedback_text(rows):
    # A deterministic query budget, not a model context or token-cost estimate.
    # Stage/status remain in the provenance projection, never become invented
    # diagnoses. Evidence paths and surrounding candidate excerpts are omitted.
    return '\n'.join(json.dumps(row['diagnostics'], ensure_ascii=False, sort_keys=True)[:2048]
                     for row in rows)


def visible_feedback(payload):
    return project_feedback(payload.get('confirmed_errors', [])
                            + payload.get('verification_uncertainty', []))
