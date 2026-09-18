"""Training-only statement libraries with explicit, bounded context exposure."""
import json
import re

from baseline_common.memory import packet
from baseline_common.retrieval import rank_records
from experiments.mixed117_study.portability import scope_matches


def words(text):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    return re.sub(r'\b[AB]_', '', text).replace('_', ' ')


def retrieve(records, task, feedback, config):
    maximum = int(config.get('memory_characters', 10000))
    audit = {'sent_programs': 0, 'sent_repairs': 0, 'sent_mechanisms': 0,
             'asset_update': False, 'test_feedback_learned': False}
    if not config.get('use_code_memory', True):
        return {'kind': 'typed_training_mechanisms', 'items': [], 'selection_audit': audit}
    query = words(task['requirement'])
    # Retrieval is independent of this task's outcome in every arm. Feedback
    # is provided only through the common, separately controlled repair path.
    meta = task.get('metadata', {})
    candidates = []
    query_types = set()
    for side in ('inputs', 'outputs', 'inouts'):
        fields = task['interface'].get(side, {})
        for field in (fields.values() if isinstance(fields, dict) else fields):
            value = field.get('type', '') if isinstance(field, dict) else field
            query_types.update(re.findall(r'\b[A-Z]+\b', str(value).upper()))
    for a in records:
        if a.get('kind') != 'learned_program_mechanism': continue
        if not scope_matches((a['target'], 'st'), (task['target'], 'st')): continue
        if task['id'] in a['source_task_ids']: continue
        if any(meta.get(k) and meta[k] in a.get('source_identities', {}).get(k, [])
               for k in ('contamination_group_id', 'semantic_signature')): continue
        required = {r['type'] for r in a['roles'] if r['direction'] == 'VAR_INPUT'}
        if query_types and not required <= query_types: continue
        meaning = ' '.join(v['name']+' '+v['description'] for v in a['role_meanings'].values())
        # Avoid selecting a pattern solely through generic PLC vocabulary.
        specific = set(re.findall(r'[a-z]{4,}', words(meaning).lower())) - {
            'subsystem','input','output','true','false','request','value','state','process','current'}
        if not specific.intersection(re.findall(r'[a-z]{4,}', query.lower())): continue
        candidates.append({'id': a['id'], 'description': words(meaning),
                           'requirement': words(' '.join(a['requirements'])), 'asset': a})
    ranked = rank_records(query, candidates, len(candidates))
    selected = []; covered = set()
    for match in ranked:
        a = match['asset']
        coverage = {v['description'] for v in a['role_meanings'].values() if v['description']}
        if selected and coverage and coverage <= covered: continue
        item = {k:a[k] for k in ('id','kind','operation_st','roles','role_meanings','footprint','guard')}
        item.update(source_task_ids=a['source_task_ids'][:3], source_group_count=a['source_group_count'],
            source_obligations=a['requirements'][:3],
            applicability={'type_prefilter': 'pass', 'semantic_role_binding': 'unknown',
                'policy': 'Match the entry-state meaning and behavior to the public contract before using a template. '
                          'If not justified, omit it. pN are placeholders, never prescribed interface names. '
                          'This is a complete ordered sequence; do not drop enclosing IF guards or state updates.'},
            evidence_scope='Typed abstraction observed in independently grouped verified training programs; '
                           'not a current-task correctness certificate',
            historical_changes=[{'before_sha256': c['before_sha256'], 'after_sha256': c['after_sha256'],
                                 'trigger_stages': c['trigger_stages'], 'causal_local_intervention': False}
                                for c in a.get('contrasts', [])[:1]])
        trial = packet(selected+[item], maximum-600, label='typed_training_mechanisms')
        if len(trial['items']) != len(selected)+1: continue
        selected.append(item); covered.update(coverage)
        if len(selected) >= int(config.get('mechanism_top_k', 3)): break
    result = packet(selected, maximum-600, label='typed_training_mechanisms')
    audit.update(eligible_mechanisms=len(candidates), sent_mechanisms=len(result['items']),
                 semantic_applicability_established_by_retrieval=False,
                 automatic_code_application=False, selection_uses_current_feedback=False)
    result['selection_audit'] = audit
    assert len(json.dumps(result, ensure_ascii=False)) <= maximum
    return result
