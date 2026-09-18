"""Link training repair evidence to original requirement IDs and properties.

These links record provenance, not inferred current-task preconditions. A
requirement named in a multi-property failure is not necessarily the unique
failed requirement. Text and formal property are kept as original records.
"""
import copy
import re

from baseline_common.utils import object_hash


def link_requirements(program, output_name, witnesses):
    if not program['task_id'].startswith('TR_') or program['metadata'].get('split') != 'train':
        raise ValueError('only original training contracts may supply links')
    requirements = program['metadata'].get('requirements', [])
    ids = [r['id'] for r in requirements]
    if len(ids) != len(set(ids)): raise ValueError('ambiguous source requirement identity')
    named = {rid for w in witnesses for rid in w.get('requirement_ids', [])}
    token = re.compile(r'\b'+re.escape(output_name)+r'\b', re.I)
    linked = []
    for ordinal, requirement in enumerate(requirements):
        text_match = bool(token.search(requirement.get('text', '')))
        property_match = bool(token.search(requirement.get('property', '')))
        listed = requirement['id'] in named
        if not (text_match or property_match or listed): continue
        reasons = []
        if listed: reasons.append('id_listed_in_source_failure')
        if property_match: reasons.append('output_named_in_source_property')
        if text_match: reasons.append('output_named_in_source_text')
        priority = 0 if listed and (property_match or text_match) else 1 if property_match else 2 if text_match else 3
        linked.append({'requirement': copy.deepcopy(requirement), 'requirement_sha256': object_hash(requirement),
                       'link_reasons': reasons, 'original_ordinal': ordinal, 'priority': priority,
                       'unique_failed_requirement_established': False})
    linked.sort(key=lambda r: (r['priority'], r['original_ordinal']))
    return {'source_task_id': program['task_id'], 'source_record_id': program['id'],
            'source_record_sha256': object_hash(program),
            'source_metadata_sha256': object_hash(program['metadata']),
            'source_output': output_name, 'requirements': linked,
            'unresolved_source_requirement_ids': sorted(named-set(ids)),
            'test_contract_learned': False, 'universal_applicability_established': False}


def context_texts(links, selected_witnesses):
    """Prioritize the selected historical witness without inventing text."""
    named = {rid for w in selected_witnesses for rid in w.get('requirement_ids', [])}
    def order(row):
        reasons = row['link_reasons']
        output_link = any(r in reasons for r in ('output_named_in_source_property', 'output_named_in_source_text'))
        selected = row['requirement']['id'] in named
        return (0 if selected and output_link else 1 if output_link else 2 if selected else 3,
                row['priority'], row['original_ordinal'])
    return list(dict.fromkeys(row['requirement']['text'] for row in sorted(links['requirements'], key=order)
                              if row['requirement'].get('text')))


def bounded_context(links, selected_witnesses, maximum_characters=1200):
    """Keep all named output obligations together, or omit the reference."""
    named = {rid for w in selected_witnesses for rid in w.get('requirement_ids', [])}
    required = {row['requirement']['text'] for row in links['requirements']
                if row['requirement']['id'] in named and row['requirement'].get('text') and
                any(r in row['link_reasons'] for r in ('output_named_in_source_property', 'output_named_in_source_text'))}
    clauses = context_texts(links, selected_witnesses)
    context = [text for text in clauses if text in required]
    if sum(map(len, context)) > maximum_characters: return None
    for text in clauses:
        if len(context) >= max(2, len(required)): break
        if text not in context and sum(map(len, context))+len(text) <= maximum_characters:
            context.append(text)
    return context or None
