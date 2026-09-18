"""Bounded training-only repair evidence for the exploratory hard-task study."""
import json
from baseline_common.memory import compatible, packet
from baseline_common.retrieval import rank_records
from .feedback import failure_stages, failure_query, diagnostic_brief
from .repair_memory import compact_repair
from .retrieval import clauses, retrieve as contract_retrieve


def locally_verified(record):
    gates = record.get('after_feedback', [])
    if not gates or any(not isinstance(g, dict) or g.get('status') != 'pass' for g in gates):
        return False
    aliases = {'compile': 'compiler', 'runtime': 'openplc_feedback', 'formal': 'plcverif'}
    names = {aliases.get(g.get('name', g.get('stage')), g.get('name', g.get('stage'))) for g in gates}
    # A single passing compile receipt is insufficient. This is coverage of
    # recorded checks, not a new replay or proof that a repair generalizes.
    return ({'compiler', 'openplc_feedback', 'plcverif'} <= names
            and any(g.get('status') == 'fail' for g in record.get('observed_failure', []))
            and bool(record.get('before_code')) and bool(record.get('after_code'))
            and record['before_code'] != record['after_code'])


def retrieve(bank, task, feedback, config):
    if config.get('asset_representation') == 'training_induced_knowledge_v1':
        from .knowledge_retrieval import retrieve as knowledge_retrieve
        return knowledge_retrieve(bank, task, feedback, config)
    if config.get('asset_representation') == 'training_repair_slices_v2':
        from .repair_retrieval import retrieve as repair_retrieve
        return repair_retrieve(bank, task, feedback, config)
    if config.get('asset_representation') == 'training_transition_coverage_v2':
        from .semantic_retrieval import retrieve as semantic_retrieve
        return semantic_retrieve(bank, task, feedback, config)
    if config.get('asset_representation') == 'typed_training_mechanisms_v1':
        from .mechanism_retrieval import retrieve as mechanism_retrieve
        return mechanism_retrieve(bank, task, feedback, config)
    eligible = compatible(bank, task)
    maximum = int(config.get('memory_characters', 16000))
    if maximum < 1024:
        raise ValueError('guarded memory budget must leave at least 1024 characters for framing and audit')
    # Do not pretend that a zero A/B coverage score establishes suitability of
    # complete training programs for an unrelated public task.
    has_contract = any(clauses(task['requirement']).values())
    use_code = has_contract and config.get('use_code_memory', True)
    code_config = dict(config, use_code_memory=use_code, use_repair_memory=False,
                       use_skill_memory=False, memory_characters=9000,
                       code_memory_characters=8500, program_top_k=1)
    codes = contract_retrieve(eligible, task, [], code_config)['items'] if use_code else []
    stages = failure_stages(feedback)
    top_k = max(0, int(config.get('repair_top_k', 2)))
    repairs = [r for r in eligible if r['kind'] == 'successful_trajectory_repair'
               and locally_verified(r) and stages.intersection(r['trigger_stages'])
               ] if config.get('use_repair_memory', True) and top_k else []
    # Index the actual historical error, not just the old task's prose.
    indexed = [{'id': str(i), 'requirement': r['requirement'],
                'description': json.dumps(diagnostic_brief(r['observed_failure'], 1800), ensure_ascii=False),
                'summary': r.get('repair_hypothesis', '')} for i, r in enumerate(repairs)]
    query = failure_query(feedback) + '\n' + task['requirement']
    selected = []
    for match in rank_records(query, indexed, int(config.get('repair_search_k', 32))):
        record = repairs[int(match['id'])]
        hint = compact_repair(record, int(config.get('repair_item_characters', 3200)), feedback)
        if hint is None:
            continue
        proposed = packet(selected + [hint], 7000, label='verified_local_repairs')
        if len(proposed['items']) != len(selected) + 1:
            continue
        selected.append(hint)
        if len(selected) == top_k:
            break
    # Repair evidence has reserved space and is first in the final packet.
    result = packet(selected + codes, maximum - 512, label='verified_local_repairs_and_contracts')
    result['selection_audit'] = {'recognized_AB_contract': has_contract,
        'eligible_immediately_verified_repairs': len(repairs),
        'sent_repairs': sum(r['kind'] == 'successful_trajectory_repair' for r in result['items']),
        'sent_programs': sum(r['kind'] == 'verified_contract_donor' for r in result['items']),
        'asset_update': False, 'no_matching_contract_action': 'omit_full_program_donors'}
    assert len(json.dumps(result, ensure_ascii=False)) <= maximum
    return result
