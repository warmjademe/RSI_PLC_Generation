"""Public-contract retrieval of source-bound, induced knowledge references.

Knowledge remains a fallible hypothesis. This module neither learns from the
query nor applies code, proves preconditions, or publishes a frozen asset bank.
"""
import copy
import json

from baseline_common.errors import ProtocolError
from baseline_common.retrieval import rank_records
from baseline_common.utils import object_hash
from experiments.mixed117_study.portability import scope_matches
from .knowledge_candidates import candidate_record
from .mechanism_retrieval import retrieve as legacy_empty, words
from .semantic_retrieval import terms
from .diagnostic_knowledge_query import active_feedback, feedback_text
from .knowledge_applicability import ANCHORED, mode as selection_mode, content_terms, screen

VERSION = 'training_induced_knowledge_v1'
KIND = 'training_knowledge_reference'
CORE = ('knowledge_need', 'representation', 'preconditions', 'payload',
        'verification_obligations', 'boundaries')
GENERIC = {'training', 'successful', 'verified', 'verification', 'structured', 'text',
           'requirements', 'requirement', 'implementation', 'programs', 'source', 'knowledge',
           'reference', 'references', 'property', 'properties'}
USAGE = (
    'These mechanisms were proposed from training histories. They are hypotheses, not verified '
    'solutions of this task. For a relevant mechanism, establish its preconditions and bind its '
    'roles to the public contract before using it; otherwise omit it. Preserve the fixed interface, '
    'scan order, state, numeric types, and all required behavior. Boundaries and verification '
    'obligations are part of the mechanism; no listed check has thereby passed. Current task '
    'feedback, if supplied separately, takes precedence over a conflicting analogy. '
    'Source quotations remain in the frozen bank. Every generated candidate needs the common checks.'
)


def require(condition, message):
    if not condition:
        raise ProtocolError(message)


def prepare_reference(candidate, records_by_id, protocol):
    """Prepare a projection from exact original sources; caller still freezes/releases it.

    The caller must first audit the real induction and its usage. Accepting a
    schema-correct fixture here is not evidence that an actual model learned it.
    """
    require(candidate.get('kind') == 'knowledge_candidate', 'expected induced candidate')
    sources = []
    for record_id, digest in candidate['source_records'].items():
        source = records_by_id.get(record_id)
        require(source is not None and object_hash(source) == digest, 'knowledge source record changed')
        sources.append(source)
    require(candidate_record(candidate['proposal'], sources, protocol) == candidate,
            'induced candidate does not match its exact source evidence')
    targets = {r['target'] for r in sources}
    require(len(targets) == 1, 'knowledge sources have incompatible targets')
    identities = {key: sorted({r.get('metadata', {}).get(key) for r in sources
                              if r.get('metadata', {}).get(key)})
                  for key in ('contamination_group_id', 'semantic_signature')}
    result = {'kind': KIND, 'representation_version': VERSION,
              'target': next(iter(targets)), 'source_task_ids': candidate['source_task_ids'],
              'source_identities': identities, 'candidate': copy.deepcopy(candidate),
              'source_protocol_sha256': protocol['protocol_sha256'],
              'reference_scope': 'exploratory_use_of_source_bound_hypothesis',
              'semantic_entailment_verified': False, 'transfer_verified': False}
    result['id'] = 'knowledge-reference:'+object_hash(result)[:24]
    return copy.deepcopy(result)


def validate_reference(reference):
    require(reference.get('kind') == KIND and reference.get('representation_version') == VERSION,
            'unexpected knowledge reference representation')
    require(reference.get('id') == 'knowledge-reference:'+object_hash(
        {k: v for k, v in reference.items() if k != 'id'})[:24], 'knowledge reference hash mismatch')
    candidate = reference['candidate']
    require(candidate.get('released_for_generation') is False
            and candidate.get('semantic_entailment_verified') is False
            and candidate.get('transfer_verified') is False
            and reference['semantic_entailment_verified'] is False
            and reference['transfer_verified'] is False, 'unsupported knowledge verification claim')
    require(reference['source_task_ids'] == candidate['source_task_ids']
            and reference['source_protocol_sha256'] == candidate['protocol_sha256'], 'knowledge lineage differs')


def without_trace_links(value):
    """Remove only source_refs columns; all mechanism content remains whole."""
    if isinstance(value, dict):
        return {k: without_trace_links(v) for k, v in value.items() if k != 'source_refs'}
    if isinstance(value, list):
        return [without_trace_links(v) for v in value]
    return copy.deepcopy(value)


def project(reference):
    validate_reference(reference)
    proposal = reference['candidate']['proposal']
    return {'id': reference['id'], 'kind': KIND,
            'knowledge': without_trace_links({k: proposal[k] for k in CORE}),
            'evidence_level': 'source_bound_induction_hypothesis'}


def source_bound_projection(item, records_by_id):
    reference = records_by_id.get(item.get('id'))
    if not reference or reference.get('kind') != KIND:
        return False
    try:
        return item == project(reference)
    except (KeyError, TypeError, ProtocolError):
        return False


def search_text(reference):
    proposal = reference['candidate']['proposal']
    # Negative boundaries and long source examples must not alone trigger use.
    # No query-specific success, diagnostic, source task name, or code is indexed.
    return words(' '.join([proposal['knowledge_need'],
        ' '.join(p['statement'] for p in proposal['preconditions']),
        json.dumps(without_trace_links(proposal['payload']), ensure_ascii=False)]))


def specific(text):
    return terms(text)-GENERIC


def retrieve(records, task, feedback, config):
    selected_mode = selection_mode(config)
    current = active_feedback(feedback, config)
    empty = legacy_empty(records, task, [], {**config, 'use_code_memory': False})
    if not config.get('use_code_memory', True):
        return empty
    maximum = int(config.get('memory_characters', 6000))
    top_k = int(config.get('mechanism_top_k', 2))
    require(1500 <= maximum <= 10000 and 1 <= top_k <= 3, 'knowledge context limits outside envelope')
    # Default remains the fixed public-only packet. The explicit new mode uses
    # current diagnostics only as a transient ranking query, never bank content.
    public = task['requirement']+'\n'+json.dumps(task['interface'], ensure_ascii=False)
    tokenize = content_terms if selected_mode == ANCHORED else specific
    query_terms = tokenize(public)
    diagnostic_text = feedback_text(current)
    diagnostic_terms = tokenize(diagnostic_text)
    meta = task.get('metadata', {})
    eligible = []
    for reference in sorted((r for r in records if r.get('kind') == KIND), key=lambda r: r['id']):
        validate_reference(reference)
        if not scope_matches((reference['target'], 'st'), (task['target'], 'st')):
            continue
        if task['id'] in reference['source_task_ids']:
            continue
        if any(meta.get(k) and meta[k] in reference['source_identities'].get(k, [])
               for k in ('contamination_group_id', 'semantic_signature')):
            continue
        if selected_mode == ANCHORED and not screen(reference['candidate']['proposal'], public)['eligible']:
            continue
        text = search_text(reference)
        if selected_mode == ANCHORED:
            text = ' '.join(sorted(content_terms(text)))
        overlap = query_terms & tokenize(text)
        diagnostic_overlap = diagnostic_terms & tokenize(text)
        if len(overlap) < 2 and not (overlap and len(diagnostic_overlap) >= 2):
            continue
        eligible.append({'id': reference['id'], 'requirement': text,
                         'reference': reference, 'overlap': overlap,
                         'diagnostic_overlap': diagnostic_overlap})
    ranking_query = (' '.join(sorted(query_terms | diagnostic_terms)) if selected_mode == ANCHORED
                     else words(public+'\n'+diagnostic_text))
    ranked = rank_records(ranking_query, eligible, len(eligible))
    selected = []; covered = set(); diagnostic_covered = set()
    core_hashes = set(); remaining = list(enumerate(ranked))
    while remaining and len(selected) < top_k:
        # Prefer additional requirement terms; BM25 order breaks ties. This is
        # lexical diversity, not a proof of requirement coverage or applicability.
        position, match = max(remaining, key=lambda row: (
            len(row[1]['diagnostic_overlap']-diagnostic_covered),
            len(row[1]['overlap']-covered), -row[0]))
        remaining = [(p, row) for p, row in remaining if p != position]
        if not (match['overlap']-covered or match['diagnostic_overlap']-diagnostic_covered):
            continue
        item = project(match['reference'])
        identity = object_hash(item['knowledge'])
        if identity in core_hashes:
            continue
        trial = {'kind': VERSION, 'items': selected+[item], 'usage_conditions': USAGE,
                 'selection_audit': {'sent_knowledge_references': len(selected)+1,
                    'sent_programs': 0,
                    'asset_update': False, 'test_feedback_learned': False,
                    'selection_uses_current_feedback': bool(current),
                    **({'knowledge_selection_mode': selected_mode} if selected_mode == ANCHORED else {}),
                    **({'current_feedback_query_sha256': object_hash(current)} if current else {}),
                    'semantic_applicability_established': False, 'automatic_code_application': False}}
        if len(json.dumps(trial, ensure_ascii=False)) > maximum:
            continue
        selected.append(item); covered.update(match['overlap']); core_hashes.add(identity)
        diagnostic_covered.update(match['diagnostic_overlap'])
    if not selected:
        # Asset absence is the exact NoAssets request, without extra guidance.
        return empty
    result = {'kind': VERSION, 'items': selected, 'usage_conditions': USAGE,
              'selection_audit': {'sent_knowledge_references': len(selected),
                  'sent_programs': 0,
                  'asset_update': False, 'test_feedback_learned': False,
                  'selection_uses_current_feedback': bool(current),
                  **({'knowledge_selection_mode': selected_mode} if selected_mode == ANCHORED else {}),
                  **({'current_feedback_query_sha256': object_hash(current)} if current else {}),
                  'semantic_applicability_established': False, 'automatic_code_application': False}}
    require(len(json.dumps(result, ensure_ascii=False)) <= maximum, 'knowledge packet exceeds context limit')
    return result
