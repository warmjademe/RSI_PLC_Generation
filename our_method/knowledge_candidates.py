"""Evidence-bound proposals in several representations, not released assets.

These checks establish structure and source traceability. They do not establish
semantic entailment, causal repair effects, or transfer to another PLC task.
"""
import copy
import json

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash, object_hash
from .curation_evidence import validate_repair_source
from .rsi_protocol import assert_training_sources, record_sources

REPRESENTATIONS = {
    'conditional_rule': {'when', 'then'},
    'parameterized_template': {'parameters', 'st'},
    'state_machine': {'states', 'initial', 'transitions'},
    'dependency_graph': {'entities', 'relations'},
    'procedure': {'steps'},
}
CHECKS = {'interface', 'static', 'runtime', 'formal', 'unresolved'}


def require(condition, message):
    if not condition:
        raise ProtocolError(message)


def fields(value, names):
    require(isinstance(value, dict) and set(value) == set(names), 'unexpected proposal fields')


def string(value):
    require(isinstance(value, str) and bool(value.strip()), 'expected nonempty string')


def items(value, minimum=1, maximum=16):
    require(isinstance(value, list) and minimum <= len(value) <= maximum, 'invalid list size')


def source_documents(records, protocol):
    """Project only explicitly whitelisted source fields; never load test paths."""
    require(len({r['id'] for r in records}) == len(records), 'duplicate source identity')
    programs = [r for r in records if r['kind'] == 'verified_program']
    require(len({r['task_id'] for r in programs}) >= 2, 'two training programs required')
    require(len({r['target'] for r in records}) == 1, 'source targets differ')
    output = {}
    for r in records:
        assert_training_sources(record_sources(r), protocol)
        if r['kind'] == 'verified_program':
            require(r['candidate_sha256'] == content_hash(r['code']), 'source code hash mismatch')
            doc = {k: r[k] for k in ['requirement', 'interface', 'code']}
        elif r['kind'] == 'successful_trajectory_repair':
            validate_repair_source(r, programs, protocol)
            doc = {k: r[k] for k in ['before_code', 'after_code']}
            doc['observed_failure'] = json.dumps(r['observed_failure'], ensure_ascii=False, sort_keys=True)
        else:
            raise ProtocolError('unsupported knowledge source')
        require(all(isinstance(v, str) for v in doc.values()), 'source document must be text')
        output[r['id']] = {'task_id': r['task_id'], 'kind': r['kind'], 'documents': doc,
                           'record_sha256': object_hash(r)}
    return output


def validate_proposal(value, records, protocol):
    """Fail closed on fabricated quotes, source identities, and relation endpoints."""
    documents = source_documents(records, protocol)
    require(isinstance(value, dict), 'proposal must be an object')
    if value.get('decision') == 'abstain':
        fields(value, {'decision', 'reason'})
        string(value['reason'])
        return copy.deepcopy(value)
    fields(value, {'decision', 'knowledge_need', 'representation', 'selection_reason',
                   'preconditions', 'payload', 'verification_obligations', 'boundaries', 'evidence'})
    require(value['decision'] == 'propose', 'invalid proposal decision')
    require(len(json.dumps(value, ensure_ascii=False)) <= 16000, 'proposal exceeds size budget')
    for key in ['knowledge_need', 'representation', 'selection_reason']:
        string(value[key])
    kind = value['representation']
    require(kind in REPRESENTATIONS, 'unsupported representation')
    items(value['evidence'], maximum=20)
    evidence, program_tasks = {}, set()
    for e in value['evidence']:
        fields(e, {'id', 'source_id', 'field', 'quote'})
        for v in e.values():
            string(v)
        require(e['id'] not in evidence, 'duplicate evidence identity')
        source = documents.get(e['source_id'])
        require(source is not None and e['field'] in source['documents'], 'unknown evidence source or field')
        require(12 <= len(e['quote']) <= 1200 and e['quote'] in source['documents'][e['field']],
                'evidence quote is absent, too short, or too large')
        evidence[e['id']] = e
        if source['kind'] == 'verified_program':
            program_tasks.add(source['task_id'])
    require(len(program_tasks) >= 2, 'proposal must cite both successful training programs')
    used = set()

    def refs(ids):
        items(ids)
        require(all(isinstance(i, str) and i in evidence for i in ids), 'unbound evidence reference')
        used.update(ids)

    for key in ['preconditions', 'verification_obligations']:
        items(value[key])
        for clause in value[key]:
            fields(clause, {'statement', 'check', 'source_refs'})
            string(clause['statement'])
            require(isinstance(clause['check'], str) and clause['check'] in CHECKS, 'invalid check kind')
            refs(clause['source_refs'])
    items(value['boundaries'])
    for boundary in value['boundaries']:
        string(boundary)
    payload = value['payload']
    fields(payload, REPRESENTATIONS[kind])
    if kind == 'conditional_rule':
        string(payload['when']); string(payload['then'])
    elif kind == 'parameterized_template':
        items(payload['parameters'])
        for parameter in payload['parameters']:
            string(parameter)
        require(len(set(payload['parameters'])) == len(payload['parameters']), 'duplicate template parameter')
        string(payload['st'])
    elif kind == 'procedure':
        items(payload['steps'])
        for step in payload['steps']:
            string(step)
    elif kind == 'state_machine':
        items(payload['states'])
        for state in payload['states']:
            string(state)
        states = set(payload['states'])
        require(len(states) == len(payload['states']), 'duplicate state')
        require(isinstance(payload['initial'], str) and payload['initial'] in states, 'unknown initial state')
        items(payload['transitions'], maximum=32)
        for edge in payload['transitions']:
            fields(edge, {'from', 'to', 'guard', 'action', 'source_refs'})
            for key in ['from', 'to', 'guard', 'action']:
                string(edge[key])
            require(edge['from'] in states and edge['to'] in states, 'unknown transition endpoint')
            refs(edge['source_refs'])
    else:
        items(payload['entities'])
        nodes = set()
        for node in payload['entities']:
            fields(node, {'id', 'type'})
            string(node['id']); string(node['type'])
            require(node['type'] in {'signal', 'state', 'function', 'property'}, 'unknown entity type')
            require(node['id'] not in nodes, 'duplicate graph entity')
            nodes.add(node['id'])
        items(payload['relations'], maximum=32)
        for edge in payload['relations']:
            fields(edge, {'from', 'to', 'relation', 'source_refs'})
            for key in ['from', 'to', 'relation']:
                string(edge[key])
            require(edge['from'] in nodes and edge['to'] in nodes, 'unknown graph endpoint')
            require(edge['relation'] in {'requires', 'inhibits', 'precedes', 'updates', 'depends_on'},
                    'unknown relation type')
            refs(edge['source_refs'])
    require(used == set(evidence), 'uncited evidence must not inflate proposal support')
    return copy.deepcopy(value)


def candidate_record(value, records, protocol):
    proposal = validate_proposal(value, records, protocol)
    require(proposal['decision'] == 'propose', 'abstention cannot become an asset candidate')
    record = {'kind': 'knowledge_candidate', 'status': 'candidate', 'proposal': proposal,
              'source_records': {r['id']: object_hash(r) for r in records},
              'source_task_ids': sorted(set().union(*(record_sources(r) for r in records))),
              'protocol_sha256': protocol['protocol_sha256'],
              'evidence_level': 'schema_checked_and_source_quotes_bound',
              'semantic_entailment_verified': False, 'transfer_verified': False,
              'released_for_generation': False}
    record['id'] = 'knowledge-candidate:' + object_hash(record)[:24]
    return record


SYSTEM = '''Propose ONE reusable PLC software knowledge item from the supplied TRAINING evidence.
Start with the knowledge need, then choose the representation that fits it. Do not prefer a graph
merely because it is available. Use mechanisms supported by BOTH successful programs. Failure/repair
pairs are observations, not proof of which edit caused success. Abstract task-specific names into
roles; state assumptions and boundaries. Do not claim a proposal is verified. If evidence does not
support a useful common mechanism, return exactly {"decision":"abstain","reason":"..."}.
Otherwise return a JSON object with exactly these keys:
decision="propose", knowledge_need, representation, selection_reason, preconditions, payload,
verification_obligations, boundaries, evidence.
preconditions and verification_obligations are arrays of objects with statement (string),
check (interface/static/runtime/formal/unresolved), source_refs (array of evidence IDs).
check specifies a FUTURE verification obligation, never a passed check.
boundaries is an array of strings. evidence is an array of objects with id, source_id, field,
quote. Quotes must be EXACT substrings (12-1200 characters) of the supplied source documents;
cite both successful programs and use each quote in an obligation, precondition, or edge.
representation selects the exact payload shape:
conditional_rule: {"when":"...","then":"..."};
parameterized_template: {"parameters":["role"],"st":"parameterized ST fragment"};
state_machine: {"states":["state"],"initial":"state","transitions":[{"from":"state",
"to":"state","guard":"...","action":"...","source_refs":["e1"]}]};
dependency_graph: {"entities":[{"id":"role","type":"signal|state|function|property"}],
"relations":[{"from":"role","to":"role","relation":"requires|inhibits|precedes|updates|depends_on",
"source_refs":["e1"]}]};
procedure: {"steps":["..."]}.
Use concise English. Keep the full JSON below 16000 characters, preferably below 5000.
No test identities, invented evaluation results, or unsupported causal claims.'''
