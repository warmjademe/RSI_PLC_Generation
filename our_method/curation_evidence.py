"""Bind training repair observations to the successful programs being abstracted.

This supplies evidence to a proposer; it does not establish patch causality or
promote a rule. Development and test records are rejected before projection.
"""
from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash
from .guarded_retrieval import locally_verified
from .repair_memory import compact_repair
from .rsi_protocol import assert_training_sources, record_sources


def validate_repair_source(repair, programs, protocol):
    assert_training_sources(record_sources(repair), protocol)
    if repair.get('kind') != 'successful_trajectory_repair' or not locally_verified(repair):
        raise ProtocolError('curation repair lacks complete recorded passing checks')
    matches = [p for p in programs if p.get('kind') == 'verified_program'
               and p.get('task_id') == repair.get('task_id')
               and p.get('target') == repair.get('target')
               and p.get('code') == repair.get('after_code')]
    if not matches:
        raise ProtocolError('repair endpoint does not match a supplied successful training program')
    for program in matches:
        assert_training_sources(record_sources(program), protocol)
        if program.get('candidate_sha256') == content_hash(program['code']):
            return
    raise ProtocolError('successful program code hash does not match the repair endpoint')


def select_repair_sources(programs, records, protocol):
    """At most one bound observation per supplied training task, stable by ID."""
    for program in programs:
        assert_training_sources(record_sources(program), protocol)
    task_ids = {p['task_id'] for p in programs}
    selected, used = [], set()
    for record in sorted(records, key=lambda r: r['id']):
        if (record.get('kind') != 'successful_trajectory_repair'
                or record.get('task_id') not in task_ids or record.get('task_id') in used):
            continue
        try:
            validate_repair_source(record, programs, protocol)
        except ProtocolError:
            continue
        if compact_repair(record, 3200) is None:
            continue
        selected.append(record)
        used.add(record['task_id'])
    return selected


def repair_projections(repairs):
    return [compact_repair(record, 3200) for record in repairs]
