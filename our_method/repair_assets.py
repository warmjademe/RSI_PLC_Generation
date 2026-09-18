"""Training-only, output-centered contrasts of adjacent recorded repairs.

The schema is supplied by the implementation; code slices and source evidence
are learned from history. No test diagnostic, repair prose, or new PLC execution
is used to infer a universal repair precondition or a causal explanation.
"""
from collections import Counter, defaultdict
import copy
import re

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash, object_hash
from .guarded_retrieval import locally_verified
from .rsi_protocol import groups
from .semantic_assets import transition, TransitionTooLarge
from .typed_fragments import (
    declarations, Parser, tokens, variables, rename, render, Unsupported,
    credited_source,
)


def expression_reads(expr):
    if expr[0] == 'var':
        return {expr[1]}
    return set().union(*(expression_reads(child) for child in expr[2:] if isinstance(child, list)))


def backward_slice(nodes, outputs):
    """Keep ordered scalar effects needed by outputs at sequence exit.

    Branches retain their original guards and priority, including empty arms.
    The returned entry references are a conservative dependency set. This is a
    reference slice, not a separately compilable replacement or a PLC verdict.
    """
    needed = set(outputs)
    kept = []
    for node in reversed(nodes):
        if node[0] == 'assign':
            target = node[1]
            if target not in needed:
                continue
            kept.append(copy.deepcopy(node))
            needed = (needed - {target}) | expression_reads(node[2])
        elif node[0] == 'if':
            branches = []
            incoming = set()
            for condition, body in node[1]:
                selected, entry = backward_slice(body, needed)
                branches.append([copy.deepcopy(condition), selected])
                incoming.update(entry)
            otherwise, entry = backward_slice(node[2], needed)
            incoming.update(entry)
            if not otherwise and not any(body for _, body in branches):
                continue
            for condition, _ in branches:
                incoming.update(expression_reads(condition))
            kept.append(['if', branches, otherwise])
            needed = incoming
        else:
            raise Unsupported('slice excludes calls, aliases and unsupported statements')
    return list(reversed(kept)), needed


def parsed_program(code):
    fields, body = declarations(code)
    nodes = Parser(tokens(body)).statements()
    reads, writes = variables(nodes)
    if any(name not in fields for name in reads + writes):
        raise Unsupported('undeclared variable in repair source')
    if any(fields[name]['direction'] == 'VAR_INPUT' for name in writes):
        raise Unsupported('repair source writes an input')
    return fields, nodes


def output_witnesses(repair, target):
    """Use structured historical witnesses, not free-form model explanations."""
    result = []
    for gate in repair['observed_failure']:
        evidence = gate.get('evidence', [])
        if not isinstance(evidence, list): continue
        for item in evidence:
            if not isinstance(item, dict): continue
            trace = item.get('trace') or {}
            if not isinstance(trace, dict): continue
            witness = {'gate': gate.get('name'), 'kind': item.get('kind'),
                'source_evidence_sha256': object_hash(item),
                'raw_log_sha256': item.get('raw_log_sha256'),
                'oracle_status': item.get('oracle_status'),
                'requirement_ids': item.get('requirement_ids', [])}
            if item.get('kind') == 'formal_counterexample':
                conditions = trace.get('violated_condition', [])
                if isinstance(conditions, str): conditions = [conditions]
                if not isinstance(conditions, list): continue
                relevant = [c for c in conditions if isinstance(c, str)
                            and re.search(r'\b'+re.escape(target)+r'\b', c, re.I)]
                if not relevant: continue
                witness.update(conditions=relevant,
                    locality='failed source property mentions output; unique faulty assignment not established')
            elif item.get('kind') == 'openplc_functional_failure':
                expected, observed = trace.get('expected', {}), trace.get('observed', {})
                if not isinstance(expected, dict) or not isinstance(observed, dict): continue
                key = next((k for k in expected if k.casefold() == target.casefold()), None)
                if key is None or key not in observed or object_hash(expected[key]) == object_hash(observed[key]): continue
                witness.update(output=key, expected=expected[key], observed=observed[key],
                    case=trace.get('case'), step=trace.get('step'), repeat=trace.get('repeat'),
                    locality='recorded runtime discrepancy on the source output; transferability not established')
            else:
                continue
            result.append(witness)
    return result


def contrasts(repair, program, maximum_characters=6000, *, declaration_scope='program',
              slice_scope='single_scan', source_syntax='scalar_if'):
    if declaration_scope not in ('program', 'output_dependency'):
        raise ValueError('unknown declaration compatibility scope')
    if slice_scope not in ('single_scan', 'cyclic_state'):
        raise ValueError('unknown output slice scope')
    normalization = None
    if source_syntax == 'scalar_if':
        before_fields, before = parsed_program(repair['before_code'])
        after_fields, after = parsed_program(repair['after_code'])
    elif source_syntax == 'scalar_if_case':
        from .case_projection import parsed_case_program
        before_fields, before, before_parse = parsed_case_program(repair['before_code'])
        after_fields, after, after_parse = parsed_case_program(repair['after_code'])
        if before_parse['case_sites'] or after_parse['case_sites']:
            normalization = {'before': before_parse, 'after': after_parse}
    else:
        raise ValueError('unknown training source syntax')
    if declaration_scope == 'program' and before_fields != after_fields:
        raise Unsupported('repair changes declarations; type transport not established')
    fields = before_fields
    descriptions = {field['name'].casefold(): field.get('description', '')
                    for side in ('inputs', 'outputs')
                    for field in program['metadata'].get('interface', {}).get(side, [])}
    found = []
    before_outputs = {name for name, info in before_fields.items() if info['direction'] == 'VAR_OUTPUT'}
    changed_outputs = sum(info['direction'] == 'VAR_OUTPUT' and name not in before_outputs
                          for name, info in after_fields.items())
    for target, info in fields.items():
        if info['direction'] != 'VAR_OUTPUT':
            continue
        if slice_scope == 'cyclic_state':
            from .scan_state import cyclic_output_slice
            if after_fields.get(target) != info:
                changed_outputs += 1
                continue
            old, old_entry, old_closure = cyclic_output_slice(fields, before, {target})
            new, new_entry, new_closure = cyclic_output_slice(after_fields, after, {target})
        else:
            old, old_entry = backward_slice(before, {target})
            new, new_entry = backward_slice(after, {target})
        if old == new and after_fields.get(target) == info:
            continue
        changed_outputs += 1
        # Count concurrently changed output declarations but do not transport
        # them. Added/removed outputs also remain part of the source edit scope.
        if after_fields.get(target) != info:
            continue
        if len(render(old)) + len(render(new)) > maximum_characters:
            continue
        reads0, writes0 = variables(old)
        reads1, writes1 = variables(new)
        names = list(dict.fromkeys([target] + reads0 + writes0 + reads1 + writes1))
        # Slice reads include guards, priority branches and entry-state uses.
        # Ignore only declaration changes outside BOTH complete output slices;
        # never transport a changed type, initialization, direction or scope.
        if any(before_fields.get(name) != after_fields.get(name) for name in names):
            continue
        if not 2 <= len(names) <= 24:
            continue
        mapping = {name: 'p'+str(i) for i, name in enumerate(names)}
        old_tree, new_tree = rename(old, mapping), rename(new, mapping)
        roles = [{'slot': mapping[name], **fields[name],
                  'reads': name in reads0 or name in reads1,
                  'writes': name in writes0 or name in writes1} for name in names]
        obligations = [r['text'] for r in program['metadata'].get('requirements', [])
                       if r.get('text') and re.search(r'\b'+re.escape(target)+r'\b', r['text'], re.I)]
        if not obligations:
            continue
        old_transition = transition({'tree': old_tree, 'roles': roles})
        new_transition = transition({'tree': new_tree, 'roles': roles})
        if slice_scope == 'cyclic_state':
            for relation, closure in ((old_transition, old_closure), (new_transition, new_closure)):
                for name in closure['retained_exit_targets']:
                    slot = mapping[name]
                    if slot not in relation['exit_values']:
                        relation['exit_values'][slot] = ['entry', slot]
                        relation['relations'].append({'from': slot, 'relation': 'entry_value_influences_exit', 'to': slot})
        signature = object_hash({'target': program['target'], 'before': old_tree,
                                 'after': new_tree, 'roles': roles})
        if slice_scope == 'cyclic_state':
            signature = object_hash({'source_contrast_signature': signature, 'slice_scope': slice_scope,
                'retained_before': sorted(mapping[n] for n in old_closure['retained_exit_targets']),
                'retained_after': sorted(mapping[n] for n in new_closure['retained_exit_targets'])})
        found.append({
            'signature': signature, 'before_tree': old_tree, 'after_tree': new_tree,
            'roles': roles, 'output_role': mapping[target],
            'before_st': render(old_tree), 'after_st': render(new_tree),
            'before_transition': old_transition, 'after_transition': new_transition,
            'entry_references_before': sorted(mapping[n] for n in old_entry),
            'entry_references_after': sorted(mapping[n] for n in new_entry),
            'source_requirements': obligations,
            'source_binding': {slot: name for name, slot in mapping.items()},
            'role_meanings': {mapping[name]: {'name': name, 'description': descriptions.get(name, '')}
                              for name in names},
            'output_witnesses': output_witnesses(repair, target),
        })
        if normalization is not None:
            found[-1]['source_control_normalization'] = copy.deepcopy(normalization)
        if slice_scope == 'cyclic_state':
            found[-1]['slice_scope'] = slice_scope
            found[-1]['cyclic_dependency_roles_before'] = sorted(mapping[n] for n in old_closure['retained_exit_targets'])
            found[-1]['cyclic_dependency_roles_after'] = sorted(mapping[n] for n in new_closure['retained_exit_targets'])
            found[-1]['source_state_closure_iterations'] = {
                'before': old_closure['closure_iterations'], 'after': new_closure['closure_iterations']}
        if declaration_scope == 'output_dependency':
            changed = {name for name in before_fields.keys() | after_fields.keys()
                       if before_fields.get(name) != after_fields.get(name)}
            found[-1]['declaration_compatibility'] = {
                'scope': declaration_scope,
                'all_slice_role_declarations_equal': True,
                'changed_declarations_outside_both_slices': sorted(changed),
                'whole_program_declarations_equal': before_fields == after_fields,
                'type_transport_performed': False,
            }
    for item in found:
        item['jointly_changed_output_slices'] = changed_outputs
    return found


def learn(programs, repairs, *, declaration_scope='program', slice_scope='single_scan', source_syntax='scalar_if'):
    if declaration_scope not in ('program', 'output_dependency'):
        raise ValueError('unknown declaration compatibility scope')
    if slice_scope not in ('single_scan', 'cyclic_state'):
        raise ValueError('unknown output slice scope')
    if source_syntax not in ('scalar_if', 'scalar_if_case'):
        raise ValueError('unknown training source syntax')
    if any(not p['task_id'].startswith('TR_') or p['metadata'].get('split') != 'train' for p in programs):
        raise ProtocolError('repair learning rejects non-training programs')
    by_task = {p['task_id']: p for p in programs}
    if len(by_task) != len(programs):
        raise ProtocolError('duplicate training identity')
    for program in programs:
        if content_hash(program['code']) != program['candidate_sha256']:
            raise ProtocolError('unbound training program')
        if not set(program.get('learning_context_task_ids', [])) <= set(by_task):
            raise ProtocolError('missing known source dependencies')
    group_of = {tid: index for index, group in enumerate(groups(programs)) for tid in group}
    occurrences = defaultdict(list)
    rejected = Counter()
    qualified = 0
    for repair in repairs:
        if repair['task_id'] not in by_task or repair.get('kind') != 'successful_trajectory_repair':
            raise ProtocolError('repair outside original training records')
        if not locally_verified(repair):
            rejected['no_immediate_complete_passing_receipts'] += 1
            continue
        qualified += 1
        program = by_task[repair['task_id']]
        try:
            learned = contrasts(repair, program, declaration_scope=declaration_scope,
                                slice_scope=slice_scope, source_syntax=source_syntax)
        except (Unsupported, TransitionTooLarge) as error:
            rejected[str(error)] += 1
            continue
        if not learned:
            rejected['no_bounded_output_contrast_with_source_requirement'] += 1
        for item in learned:
            occurrences[item['signature']].append((program, repair, item))
    assets = []
    graph_nodes = {}
    graph_edges = []
    for signature in sorted(occurrences):
        sources = sorted(occurrences[signature], key=lambda x: (x[0]['task_id'], x[1]['id']))
        program, _, item = sources[0]
        evidence = []
        for p, repair, observed in sources:
            evidence.append({
                'task_id': p['task_id'], 'repair_id': repair['id'], 'group': group_of[p['task_id']],
                'repair_record_sha256': object_hash(repair),
                'before_code_sha256': content_hash(repair['before_code']),
                'after_code_sha256': content_hash(repair['after_code']),
                'before_slice_sha256': content_hash(render(rename(observed['before_tree'], observed['source_binding']))),
                'after_slice_sha256': content_hash(render(rename(observed['after_tree'], observed['source_binding']))),
                'binding': observed['source_binding'], 'role_meanings': observed['role_meanings'],
                'source_requirements': observed['source_requirements'],
                'output_witnesses': observed['output_witnesses'],
                'trigger_stages': repair['trigger_stages'],
                'after_gate_statuses': [{'name': gate.get('name', gate.get('stage')), 'status': gate['status']}
                                       for gate in repair['after_feedback']],
                'recorded_checks': [{'phase': phase, 'name': gate.get('name', gate.get('stage')),
                                     'status': gate['status'], 'record_sha256': object_hash(gate)}
                                    for phase, gates in [('before', repair['observed_failure']),
                                                         ('after', repair['after_feedback'])]
                                    for gate in gates],
                'jointly_changed_output_slices': observed['jointly_changed_output_slices'],
                'origin': p.get('origin', 'unknown'), 'credited_model_generated_group': credited_source(p),
                'upstream_independence_established': False, 'causal_attribution_established': False,
            })
            if declaration_scope == 'output_dependency':
                evidence[-1]['declaration_compatibility'] = observed['declaration_compatibility']
            if slice_scope == 'cyclic_state':
                evidence[-1]['source_state_closure_iterations'] = observed['source_state_closure_iterations']
            if 'source_control_normalization' in observed:
                evidence[-1]['source_control_normalization'] = observed['source_control_normalization']
        lineage = sorted({tid for p, _, _ in sources for tid in [p['task_id'], *p.get('learning_context_task_ids', [])]})
        aid = 'repair-transition:'+signature[:24]
        asset = {
            'id': aid, 'kind': 'learned_repair_transition', 'representation_version': 'training_repair_slices_v2',
            'status': 'observed_training_contrast_not_a_universal_rule',
            'task_id': program['task_id'], 'target': program['target'], 'output_language': 'st',
            'metadata': program['metadata'], 'source_task_ids': lineage,
            'source_identities': {key: sorted({by_task[t]['metadata'][key] for t in lineage if by_task[t]['metadata'].get(key)})
                                  for key in ('contamination_group_id', 'semantic_signature')},
            **{key: item[key] for key in ('signature', 'before_tree', 'after_tree', 'roles', 'output_role',
                'before_st', 'after_st', 'before_transition', 'after_transition',
                'entry_references_before', 'entry_references_after')},
            'evidence': evidence,
            'source_group_count': len({e['group'] for e in evidence if e['credited_model_generated_group']}),
            'witness_linked_source_groups': len({e['group'] for e in evidence if e['output_witnesses']}),
            'observed_task_groups': len({e['group'] for e in evidence}),
            'upstream_lineage_complete': False, 'source_policy_version': 'origin_credit_v2',
            'transfer_generation_verified': False, 'automatic_patch_allowed': False,
            'guard': {'source_requirements_are_universal_preconditions': False,
                      'current_public_contract_requires_role_binding': True,
                      'reference_slices_are_standalone_programs': False,
                      'preserve_entry_values_types_initialization_and_order': True},
        }
        if slice_scope == 'cyclic_state':
            asset.update({key: item[key] for key in ('slice_scope',
                          'cyclic_dependency_roles_before', 'cyclic_dependency_roles_after')})
            asset['guard']['dependency_scope'] = 'current output and persistent states read by its future scans'
            asset['guard']['arithmetic_definedness_or_termination_proven'] = False
        assets.append(asset)
        graph_nodes[aid] = {'id': aid, 'type': 'ObservedRepairContrast'}
        for phase in ('before', 'after'):
            node_id = aid+':'+phase
            graph_nodes[node_id] = {'id': node_id, 'type': 'OrderedOutputSlice', 'phase': phase}
            graph_edges.append({'from': aid, 'relation': 'has_'+phase+'_slice', 'to': node_id})
            for relation in asset[phase+'_transition']['relations']:
                graph_edges.append({'from': aid+':'+relation['from'],
                    'relation': 'entry_reference_in_'+phase+'_exit_expression',
                    'to': aid+':'+relation['to'], 'evidence': 'conservative_static_reference'})
        for role in asset['roles']:
            rid = aid+':'+role['slot']
            graph_nodes[rid] = {'id': rid, 'type': 'TypedRole', 'plc_type': role['type'],
                               'storage': role['direction'], 'initial': role['initial']}
            graph_edges.append({'from': aid, 'relation': 'has_role', 'to': rid})
            if slice_scope == 'cyclic_state':
                for phase in ('before', 'after'):
                    if role['slot'] in asset['cyclic_dependency_roles_'+phase]:
                        graph_edges.append({'from': aid+':'+phase, 'relation': 'retains_exit_for_cyclic_output_dependency',
                                            'to': rid, 'evidence': 'persistent_entry_reference_fixed_point'})
        for observed in evidence:
            eid = observed['repair_id']
            graph_nodes[eid] = {'id': eid, 'type': 'RecordedTrainingRepair',
                               'record_sha256': observed['repair_record_sha256']}
            graph_edges.append({'from': aid, 'relation': 'observed_in', 'to': eid})
            for phase, receipt in observed.get('source_control_normalization', {}).items():
                normalization_id = 'source-control-normalization:'+object_hash([eid, phase, receipt])
                graph_nodes[normalization_id] = {'id': normalization_id, 'type': 'SourceControlNormalization',
                                                'phase': phase, **receipt}
                graph_edges.append({'from': eid, 'relation': 'has_source_control_normalization',
                                    'to': normalization_id})
                graph_edges.append({'from': aid, 'relation': 'reference_uses_source_normalization',
                                    'to': normalization_id})
            for witness in observed['output_witnesses']:
                wid = 'source-output-witness:'+object_hash([eid, witness])
                graph_nodes[wid] = {'id': wid, 'type': 'RecordedSourceOutputWitness', **witness}
                graph_edges.append({'from': eid, 'relation': 'has_recorded_output_witness', 'to': wid})
                graph_edges.append({'from': aid, 'relation': 'target_referenced_by_source_witness', 'to': wid})
            for index, check in enumerate(observed['recorded_checks']):
                check_id = eid+':check:'+str(index)
                graph_nodes[check_id] = {'id': check_id, 'type': 'RecordedValidationReceipt', **check}
                graph_edges.append({'from': eid, 'relation': 'has_recorded_check', 'to': check_id})
            for text in observed['source_requirements']:
                qid = 'source-clause:'+object_hash([observed['task_id'], text])
                graph_nodes[qid] = {'id': qid, 'type': 'SourceRequirementClause',
                                    'task_id': observed['task_id'], 'text': text}
                graph_edges.append({'from': eid, 'relation': 'subject_to_source_clause', 'to': qid})
    summary = {
        'training_programs': len(programs), 'historical_repairs': len(repairs),
        'immediately_verified_repairs': qualified, 'excluded_repairs': dict(rejected),
        'learned_contrasts': len(assets), 'source_supported_by_multiple_credited_groups':
            sum(a['source_group_count'] >= 2 for a in assets),
        'observational_single_group_contrasts': sum(a['observed_task_groups'] == 1 for a in assets),
        'contrasts_with_output_witness': sum(a['witness_linked_source_groups'] > 0 for a in assets),
        'representation_version': 'training_repair_slices_v2', 'source_policy_version': 'origin_credit_v2',
        'model_calls': 0, 'additional_plc_executions': 0, 'training_partition': 'all_1000_no_holdout',
        'test_data_read': False, 'transfer_efficacy_established': False,
        'repair_hypothesis_used_as_fact': False, 'selected_content_sha256': object_hash(assets),
    }
    if declaration_scope == 'output_dependency':
        summary.update(declaration_compatibility_scope=declaration_scope,
            contrasts_from_changed_program_declarations=sum(any(
                not e['declaration_compatibility']['whole_program_declarations_equal']
                for e in a['evidence']) for a in assets),
            witnessed_contrasts_from_changed_program_declarations=sum(any(
                e['output_witnesses'] and not e['declaration_compatibility']['whole_program_declarations_equal']
                for e in a['evidence']) for a in assets))
    if slice_scope == 'cyclic_state':
        summary.update(slice_scope=slice_scope,
            contrasts_retaining_extra_state_targets=sum(any(
                set(a['cyclic_dependency_roles_'+phase]) - {a['output_role']} for phase in ('before', 'after'))
                for a in assets))
    if source_syntax != 'scalar_if':
        summary.update(source_syntax=source_syntax,
            contrasts_with_case_source_normalization=sum(any('source_control_normalization' in e
                                                             for e in a['evidence']) for a in assets),
            witnessed_contrasts_with_case_source_normalization=sum(any(
                'source_control_normalization' in e and e['output_witnesses'] for e in a['evidence']) for a in assets))
    return assets, summary, {'nodes': list(graph_nodes.values()), 'edges': graph_edges}
