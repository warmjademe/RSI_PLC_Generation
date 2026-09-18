"""Bounded use of source-witness-linked output repairs, with no asset updates."""
import copy
import json
import re

from baseline_common.retrieval import rank_records
from experiments.mixed117_study.portability import scope_matches
from .feedback import failure_stages, failure_query
from .mechanism_retrieval import retrieve as empty_or_legacy, words
from .semantic_retrieval import terms
from .typed_fragments import TYPES, Parser, tokens


def gate_class(name):
    return {'compiler': 'compile', 'plcverif': 'formal',
            'openplc_feedback': 'runtime', 'openplc_confirmation': 'runtime'}.get(name, name)


def interface_types(task, sides):
    result = set()
    for side in sides:
        fields = task['interface'].get(side, {})
        for field in (fields.values() if isinstance(fields, dict) else fields):
            value = field.get('type', '') if isinstance(field, dict) else field
            result.update(set(re.findall(r'\b[A-Z]+\b', str(value).upper())) & TYPES)
    return result


def project(asset, evidence, witnesses, *, view='contrast'):
    if view not in ('contrast', 'after_only'):
        raise ValueError('unsupported repair reference view')
    clauses = list(dict.fromkeys(evidence['source_requirements']))
    if 'source_contract_links' in evidence:
        from .source_contract_links import context_texts, bounded_context
        clauses = context_texts(evidence['source_contract_links'], witnesses[:1])
        context = bounded_context(evidence['source_contract_links'], witnesses[:1])
    else:
        context = []
        for text in clauses:
            if sum(map(len, context)) + len(text) <= 1200:
                context.append(text)
            if len(context) == 2: break
    if not context: return None
    roles = []
    for role in asset['roles']:
        meaning = evidence['role_meanings'][role['slot']]
        roles.append({'role': role['slot'], 'type': role['type'], 'storage': role['direction'],
                      'initial': role['initial'], 'source_name': meaning['name'],
                      'description_excerpt': meaning['description'][:80]})
    witness = witnesses[0]
    if witness['kind'] == 'formal_counterexample':
        observed = {'kind': witness['kind'], 'conditions': witness['conditions'],
                    'oracle_status': witness['oracle_status']}
    else:
        observed = {key: witness[key] for key in ('kind', 'output', 'expected', 'observed', 'case', 'step', 'repeat')}
    item = {'id': asset['id'], 'kind': asset['kind'], 'output_role': asset['output_role'],
        'source_task_id': evidence['task_id'], 'source_repair_id': evidence['repair_id'],
        'before_st': asset['before_st'], 'after_st': asset['after_st'], 'roles': roles,
        'source_context': context, 'source_context_total_clauses': len(clauses),
        'source_failure_witness': observed, 'source_witness_total_count': len(evidence['output_witnesses']),
        'after_source_checks': evidence['after_gate_statuses'],
        'entry_references_before': asset['entry_references_before'],
        'entry_references_after': asset['entry_references_after'],
        'jointly_changed_source_outputs': evidence['jointly_changed_output_slices'],
        'observed_task_groups': asset['observed_task_groups'],
        'source_record_sha256': evidence['repair_record_sha256']}
    if asset.get('slice_scope') == 'cyclic_state':
        item.update(source_slice_scope='cyclic_state_dependency_closure',
                    retained_exit_roles_before=asset['cyclic_dependency_roles_before'],
                    retained_exit_roles_after=asset['cyclic_dependency_roles_after'])
        # Repeated column names and indentation do not carry training behavior.
        # Preserve every role value and full statement tree in a smaller packet.
        columns = ['role', 'type', 'storage', 'initial', 'source_name', 'description_excerpt']
        item['role_columns'] = columns
        item['roles'] = [[role[column] for column in columns] for role in roles]
        for phase in ('before', 'after'):
            compact = '\n'.join(line.strip() for line in asset[phase+'_st'].splitlines() if line.strip())
            if Parser(tokens(compact)).statements() != asset[phase+'_tree']:
                raise ValueError('compact projection changed the complete statement tree')
            item[phase+'_st'] = compact
        item['source_projection'] = 'complete_code_and_role_table_v1'
    if 'source_control_normalization' in evidence:
        item['source_control_normalization'] = {
            'method': 'integer_variable_case_to_if_v1',
            'phases_with_case': [phase for phase in ('before', 'after')
                                if evidence['source_control_normalization'][phase]['case_sites']],
            'scope': 'source CASE represented as ordered IF; selected body and unmatched path preserved',
        }
    if view == 'after_only':
        # Keep the full contrast in the bank. Only the model-facing view is
        # smaller; the repaired slice and its cyclic state closure are intact.
        for key in ('before_st', 'entry_references_before', 'retained_exit_roles_before'):
            item.pop(key, None)
        item['source_reference_view'] = view
        item['source_projection'] = 'complete_after_slice_with_source_roles_v1'
        item['source_witness_scope'] = 'The failure witness describes the omitted BEFORE source, not this repaired slice.'
    return copy.deepcopy(item)


def source_bound_projection(item, records_by_id):
    view = item.get('source_reference_view', 'contrast')
    if view not in ('contrast', 'after_only'): return False
    asset = records_by_id.get(item.get('id'), {})
    if asset.get('representation_version') != 'training_repair_slices_v2': return False
    return any(item == project(asset, evidence, [witness], view=view)
               for evidence in asset['evidence']
               if evidence['repair_id'] == item.get('source_repair_id')
               and evidence['task_id'] == item.get('source_task_id')
               for witness in evidence['output_witnesses'])


def predicate_binding_obligation(asset, repair_id, public_input_types):
    """An obligation to establish a correspondence, never an inferred binding."""
    if 'BOOL' in public_input_types: return None
    roles = sorted(r['slot'] for r in asset['roles']
                   if r['direction'] == 'VAR_INPUT' and r['type'] == 'BOOL')
    if not roles: return None
    return {'asset_id': asset['id'], 'source_repair_id': repair_id,
            'source_BOOL_input_roles': roles,
            'obligation': 'Use this analogy only if the source BOOL roles can be defined by the current public '
                          'requirements and permitted internal state. Preserve the fixed external interface; '
                          'do not add source input ports or invent task conditions. Otherwise ignore the reference.',
            'role_binding_established': False}


def source_bound_binding_obligations(memory, records_by_id, task, config):
    expected = []
    if config.get('repair_boolean_role_binding', 'public_inputs') == 'task_conditions':
        inputs = interface_types(task, ('inputs', 'inouts'))
        for item in memory['items']:
            if not source_bound_projection(item, records_by_id): return False
            obligation = predicate_binding_obligation(records_by_id[item['id']], item['source_repair_id'], inputs)
            if obligation is not None: expected.append(obligation)
    if not expected: return 'predicate_binding_obligations' not in memory
    return memory.get('predicate_binding_obligations') == expected


def retrieve(records, task, feedback, config):
    view = config.get('repair_reference_view', 'contrast')
    if view not in ('contrast', 'after_only'):
        raise ValueError('unsupported repair reference view')
    selection_view = config.get('repair_selection_view', 'visible')
    if selection_view not in ('visible', 'contrast') or (selection_view == 'contrast' and view != 'after_only'):
        raise ValueError('unsupported repair selection view')
    boolean_binding = config.get('repair_boolean_role_binding', 'public_inputs')
    if boolean_binding not in ('public_inputs', 'task_conditions'):
        raise ValueError('unsupported BOOL role binding policy')
    empty = empty_or_legacy(records, task, [], {**config, 'use_code_memory': False})
    if not config.get('use_code_memory', True): return empty
    maximum = int(config.get('memory_characters', 4500))
    if maximum < 1500: raise ValueError('insufficient repair context envelope')
    visible_feedback = feedback if config.get('use_current_task_feedback', True) else []
    stages = {gate_class(s) for s in failure_stages(visible_feedback)}
    public = task['requirement'] + '\n' + json.dumps(task['interface'], ensure_ascii=False)
    query_terms = terms(public)
    inputs = interface_types(task, ('inputs', 'inouts'))
    compatible_inputs = inputs | {'BOOL'} if boolean_binding == 'task_conditions' else inputs
    outputs = interface_types(task, ('outputs', 'inouts'))
    metadata = task.get('metadata', {})
    indexed = []
    for asset in records:
        if asset.get('representation_version') != 'training_repair_slices_v2': continue
        if not scope_matches((asset['target'], 'st'), (task['target'], 'st')): continue
        if task['id'] in asset['source_task_ids']: continue
        if any(metadata.get(k) and metadata[k] in asset['source_identities'].get(k, [])
               for k in ('contamination_group_id', 'semantic_signature')): continue
        required = {r['type'] for r in asset['roles'] if r['direction'] == 'VAR_INPUT'}
        output_type = next(r['type'] for r in asset['roles'] if r['slot'] == asset['output_role'])
        if inputs and not required <= compatible_inputs: continue
        if outputs and output_type not in outputs: continue
        for evidence in asset['evidence']:
            witnesses = [w for w in evidence['output_witnesses']
                         if not stages or gate_class(w['gate']) in stages]
            if not witnesses: continue
            source_text = ' '.join(evidence['source_requirements'])
            meanings = ' '.join(m['name']+' '+m['description'] for m in evidence['role_meanings'].values())
            # Relevance does not prove semantic applicability. Stage matching
            # is used only after an actual visible failure, never for unknown.
            if len(query_terms & terms(source_text+' '+meanings)) < 2: continue
            indexed.append({'id': asset['id'], 'requirement': words(source_text),
                            'description': words(meanings),
                            'summary': json.dumps(witnesses, ensure_ascii=False),
                            'asset': asset, 'evidence': evidence, 'witnesses': witnesses})
    query = words(public)
    if stages: query += '\n'+failure_query(visible_feedback)
    result = {'kind': 'source_witness_linked_output_repairs', 'items': [],
        'usage_conditions':
            'These are observations from TRAINING tasks, not current-task faults or universal repair rules. '
            'Bind roles and source obligations to the current public contract before using an analogy. '
            'The slices preserve source order, guards, types and entry values; they are not standalone patches. '
            'A source witness mentioning an output does not establish a unique cause. '
            'Unknown current results remain unknown. Every candidate requires the common checks.',
        'selection_audit': {'sent_repairs': 0, 'eligible_source_evidence_records': len(indexed),
            'selection_uses_current_feedback': bool(stages), 'asset_update': False,
            'test_feedback_learned': False, 'semantic_applicability_established': False,
            'automatic_code_application': False}}
    selected = set()
    selection_items = []
    for match in rank_records(query, indexed, len(indexed)):
        if match['asset']['id'] in selected: continue
        item = project(match['asset'], match['evidence'], match['witnesses'], view=view)
        if item is None: continue
        proposed = {**result, 'items': result['items']+[item],
                    'selection_audit': {**result['selection_audit'], 'sent_repairs': len(selected)+1}}
        if boolean_binding == 'task_conditions':
            obligation = predicate_binding_obligation(match['asset'], match['evidence']['repair_id'], inputs)
            if obligation is not None:
                proposed['predicate_binding_obligations'] = result.get('predicate_binding_obligations', [])+[obligation]
        if selection_view == 'contrast':
            # Select exactly as the original contrast workflow, then change
            # only its visible view. Compression cannot admit a larger asset.
            selection_item = project(match['asset'], match['evidence'], match['witnesses'])
            selection_packet = {**proposed, 'items': selection_items+[selection_item]}
            if len(json.dumps(selection_packet, ensure_ascii=False)) > maximum: continue
            if len(json.dumps(proposed, ensure_ascii=False)) > maximum:
                raise ValueError('after-only view exceeds the selected contrast envelope')
            selection_items.append(selection_item)
        elif len(json.dumps(proposed, ensure_ascii=False)) > maximum: continue
        result = proposed
        selected.add(item['id'])
        if len(selected) >= int(config.get('mechanism_top_k', 1)): break
    # Empty retrieval is byte-equivalent to the asset-disabled condition.
    # It must not add a meta-prompt intervention without actual training assets.
    return result if result['items'] else empty
