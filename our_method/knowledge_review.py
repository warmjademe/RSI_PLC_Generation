"""Training-source consistency review, without PLC execution or test feedback.

Quotation and coverage checks establish traceability, not semantic truth. A
reviewer's judgment never promotes a candidate to verified knowledge.
"""
import copy
import json
import math

from baseline_common.utils import object_hash
from .knowledge_candidates import (
    SYSTEM as PROPOSAL_SCHEMA, candidate_record, fields, items, require,
    source_documents, string, validate_proposal,
)

VERSION = 'training_source_consistency_review_v1'
ASSESSMENTS = {'supported', 'exception_missing', 'contradicted', 'unresolved'}


def claim_index(proposal):
    """Review the mechanism and every condition, obligation, and boundary."""
    claims = [{'id': 'mechanism', 'pointer': '/payload'}]
    for key in ('preconditions', 'verification_obligations', 'boundaries'):
        claims.extend({'id': f'{key}:{i}', 'pointer': f'/{key}/{i}'}
                      for i in range(len(proposal[key])))
    return claims


def review_payload(parent, records, protocol):
    require(candidate_record(parent['proposal'], records, protocol) == parent,
            'parent candidate or its training sources changed')
    documents = source_documents(records, protocol)
    programs = {key: value for key, value in documents.items()
                if value['kind'] == 'verified_program'}
    # Full successful programs expose reset, disable, and scan-order exceptions.
    # Repair records remain bound, but only previously cited excerpts are sent.
    repair_excerpts = []
    for evidence in parent['proposal']['evidence']:
        if evidence['source_id'] not in programs:
            repair_excerpts.append({key: evidence[key] for key in ('source_id', 'field', 'quote')})
    return {'review_protocol': VERSION, 'parent_candidate_sha256': object_hash(parent),
            'protocol_sha256': protocol['protocol_sha256'], 'programs': programs,
            'repair_excerpts': repair_excerpts, 'proposal': copy.deepcopy(parent['proposal']),
            'claims': claim_index(parent['proposal']),
            'scope': 'Training-source consistency only; semantics and transfer unverified.'}


def _supplied_quote(evidence, payload):
    source = payload['programs'].get(evidence['source_id'])
    if source:
        return evidence['quote'] in source['documents'].get(evidence['field'], '')
    return any(evidence['source_id'] == old['source_id']
               and evidence['field'] == old['field'] and evidence['quote'] in old['quote']
               for old in payload['repair_excerpts'])


def _clause(proposal, pointer):
    """Only references to entire mechanism/clauses, not arbitrary JSON paths."""
    valid = {entry['pointer'] for entry in claim_index(proposal)}
    require(isinstance(pointer, str) and pointer in valid, 'unknown revision clause')
    parts = pointer[1:].split('/')
    return proposal[parts[0]] if len(parts) == 1 else proposal[parts[0]][int(parts[1])]


def validate_review(value, parent, records, protocol):
    payload = review_payload(parent, records, protocol)
    fields(value, {'decision', 'reason', 'evidence', 'findings', 'proposal'})
    require(isinstance(value['decision'], str) and value['decision'] in {'retain', 'revise', 'abstain'},
            'invalid review decision')
    string(value['reason'])
    require(len(json.dumps(value, ensure_ascii=False)) <= 48000, 'review exceeds size budget')
    items(value['evidence'], minimum=0, maximum=40)
    evidence = {}
    for item in value['evidence']:
        fields(item, {'id', 'source_id', 'field', 'quote'})
        for field in item.values():
            string(field)
        require(item['id'] not in evidence, 'duplicate review evidence')
        require(12 <= len(item['quote']) <= 1200 and _supplied_quote(item, payload),
                'review quote absent from supplied training text')
        evidence[item['id']] = item

    claims = {item['id']: item['pointer'] for item in payload['claims']}
    items(value['findings'], minimum=0, maximum=len(claims))
    seen, used = set(), set()
    program_ids = set(payload['programs'])
    revised = value['proposal']
    if value['decision'] == 'revise':
        revised = validate_proposal(revised, records, protocol)
        require(revised['decision'] == 'propose', 'revision must be a complete proposal')
        require(any(revised[k] != parent['proposal'][k] for k in
                    ('payload', 'preconditions', 'verification_obligations', 'boundaries')),
                'revision changed no substantive clause')
        require(all(_supplied_quote(item, payload) for item in revised['evidence']),
                'revision cites a repair excerpt not supplied for review')
    else:
        require(revised is None, 'retain and abstain cannot replace the proposal')

    for finding in value['findings']:
        fields(finding, {'claim_id', 'assessment', 'explanation', 'source_refs', 'resolution_refs'})
        claim = finding['claim_id']
        require(isinstance(claim, str) and claim in claims and claim not in seen,
                'unknown or duplicate reviewed claim')
        seen.add(claim)
        string(finding['explanation'])
        require(isinstance(finding['assessment'], str) and finding['assessment'] in ASSESSMENTS,
                'invalid claim assessment')
        refs = finding['source_refs']
        items(refs, maximum=40)
        require(all(isinstance(ref, str) and ref in evidence for ref in refs),
                'unbound review evidence')
        require(len(refs) == len(set(refs)), 'duplicate review source reference')
        require({evidence[ref]['source_id'] for ref in refs} >= program_ids,
                'each reviewed claim must cite both successful programs')
        used.update(refs)
        resolutions = finding['resolution_refs']
        items(resolutions, minimum=0)
        if value['decision'] == 'revise':
            for pointer in resolutions:
                _clause(revised, pointer)
            if finding['assessment'] in {'exception_missing', 'contradicted'}:
                require(resolutions, 'identified defect lacks a revised clause reference')
                old_clauses = [_clause(parent['proposal'], entry['pointer'])
                               for entry in payload['claims']]
                require(any(_clause(revised, pointer) not in old_clauses for pointer in resolutions),
                        'defect resolution references only unchanged clauses')
        else:
            require(not resolutions, 'non-revision cannot have resolution references')

    require(used == set(evidence), 'unused review evidence')
    if value['decision'] != 'abstain':
        require(seen == set(claims), 'review omitted a proposal claim')
        require(all(f['assessment'] != 'unresolved' for f in value['findings']),
                'unresolved source consistency requires abstention')
    if value['decision'] == 'retain':
        require(all(f['assessment'] == 'supported' for f in value['findings']),
                'retain decision conflicts with identified defects')
    return copy.deepcopy(value)


def reviewed_candidate(value, parent, records, protocol):
    review = validate_review(value, parent, records, protocol)
    if review['decision'] == 'abstain':
        return None
    proposal = parent['proposal'] if review['decision'] == 'retain' else review['proposal']
    return candidate_record(proposal, records, protocol)


def remaining_budget(parent_budget):
    """Subtract accounted induction usage; do not grant a new per-pair budget.

    Elapsed time here is cumulative active RunContext time. Idle time between
    phases is excluded, which must be disclosed as a scheduling amendment.
    """
    limits = copy.deepcopy(parent_budget['limits'])
    require(parent_budget['estimated_charge_calls'] == 0, 'parent charges require reconciliation')
    for key in ('model_calls', 'input_tokens', 'output_tokens', 'total_tokens', 'candidates', 'tool_calls'):
        require(type(parent_budget[key]) is int and parent_budget[key] >= 0,
                'invalid parent usage counter')
    require(parent_budget['total_tokens'] == parent_budget['input_tokens'] + parent_budget['output_tokens'],
            'parent token totals disagree')
    elapsed = parent_budget['elapsed_seconds']
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0,
            'invalid parent active time')
    require(parent_budget['candidates'] == 0 and parent_budget['tool_calls'] == 0,
            'review parent must be training-only induction')
    for cap, used in [('max_model_calls', 'model_calls'), ('max_total_tokens', 'total_tokens'),
                      ('max_wall_seconds', 'elapsed_seconds')]:
        require(parent_budget[used] <= limits[cap], 'parent already exceeded its budget')
        limits[cap] -= parent_budget[used]
    # A zero remainder is a terminal, zero-dispatch outcome, not a new context.
    return limits


SYSTEM = '''Review the supplied candidate using ONLY the original TRAINING text.
This is source-consistency review, not a new PLC test or proof of correctness.
Read both full successful programs. For every indexed claim, inspect initialization,
reset/stop/disable branches, scan ordering, retained state versus output defaults,
and exceptions to normal-operation guards. Check whether the claim's scope is
supported by BOTH sources. Historical repairs are observations, not causal proof.
Do not infer transfer to another task. No test feedback is supplied or permitted.
Return exactly {"decision":"retain|revise|abstain", "reason":"...",
"evidence":[{"id":"e1","source_id":"...","field":"code","quote":"..."}],
"findings":[{"claim_id":"...", "assessment":"supported|exception_missing|contradicted|unresolved",
"explanation":"...", "source_refs":["e1","e2"], "resolution_refs":[]}], "proposal":null}.
Every finding cites exact supplied text from BOTH successful programs using evidence
IDs. Evidence can be shared across findings. Quotes must be 12-1200 characters;
every evidence ID must be used. Review every indexed claim once for retain/revise.
Retain requires every assessment supported and proposal null. If any claim remains
unresolved, abstain with proposal null; partial findings or empty findings/evidence
are allowed for abstention. Do not label an omitted exception supported.
For revise, return a complete replacement proposal with the schema below. Each
exception_missing/contradicted finding must reference changed replacement clauses
in resolution_refs: /payload, /preconditions/N, /verification_obligations/N, or
/boundaries/N (zero-based). These references explain your proposed correction;
they are not a verified proof that it resolves the issue. Use no resolution refs
for retain/abstain. Change substantive clauses, not just titles or citations.
Use concise English; prefer shared evidence over repeated quotations. Full response
must be below 48000 characters. Source consistency and transfer remain unverified.
Replacement proposal schema follows (its decision must be propose). These limits
apply to the replacement proposal inside the outer review envelope:
''' + PROPOSAL_SCHEMA.split('Otherwise return a JSON object with exactly these keys:\n', 1)[1]
