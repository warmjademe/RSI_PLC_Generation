"""Small, source-bound procedural memories distilled from successful training code."""
import json
from pathlib import Path
import re

from baseline_common.datasets import sha
from baseline_common.errors import ProtocolError
from baseline_common.utils import object_hash
from .rsi_protocol import assert_training_sources, record_sources

FIELDS = ['trigger','preconditions','procedure','verification','boundary']


def validate_content(value):
    if not isinstance(value,dict) or set(value)!=set(FIELDS):
        raise ProtocolError('skill must contain the five declared procedural fields')
    if any(not isinstance(value[k],str) or not value[k].strip() for k in FIELDS):
        raise ProtocolError('skill fields must be nonempty strings')
    if len(json.dumps(value,ensure_ascii=False))>1000:
        raise ProtocolError('skill exceeds the fixed compact-content budget')
    if re.search(r'\bTE_C\d',json.dumps(value)):
        raise ProtocolError('test identity appeared in a learned skill')
    return {k:value[k].strip() for k in FIELDS}


def skill_record(content, sources, *, audit_directory, protocol, parent):
    content=validate_content(content)
    ids=set().union(*(record_sources(r) for r in sources))
    assert_training_sources(ids,protocol)
    primary_ids={r['task_id'] for r in sources if r['kind']=='verified_program'}
    if len(primary_ids)<2 or len({r['target'] for r in sources})!=1:
        raise ProtocolError('a skill needs at least two training tasks on the same target')
    from .curation_evidence import validate_repair_source
    for source in sources:
        if source['kind'] == 'successful_trajectory_repair':
            validate_repair_source(source, sources, protocol)
    audit_directory=Path(audit_directory).resolve()
    response=audit_directory/'proposal.json'
    if json.loads(response.read_text())!=content:
        raise ProtocolError('skill differs from the archived curated proposal')
    binding={'source_records':{r['id']:object_hash(r) for r in sources},
             'audit_directory':str(audit_directory),'proposal_sha256':sha(response),
             'input_sha256':sha(audit_directory/'input.json'),
             'model_artifact_sha256':{str(p.relative_to(audit_directory)):sha(p) for p in sorted((audit_directory/'context/model').glob('*/*.json'))},
             'protocol_sha256':protocol['protocol_sha256'],'parent':parent}
    record={**content,'kind':'verified_skill','target':sources[0]['target'],'output_language':'st',
            'evidence_task_ids':sorted(ids),'source_binding':binding,
            'requirement':' '.join(content.values()),'interface':'','metadata':{},
            'scope':'procedural hypothesis grounded in verified training programs; deployment requires a paired development gate'}
    record['id']='skill:'+object_hash(record)[:24]
    return record


def verify_skill(record, available, protocol):
    validate_content({k:record[k] for k in FIELDS})
    binding=record['source_binding']
    if binding['protocol_sha256']!=protocol['protocol_sha256']:
        raise ProtocolError('skill belongs to another learning protocol')
    sources=[]
    for rid,expected in binding['source_records'].items():
        source=available.get(rid)
        if source is None or source['kind'] not in ['verified_program','verified_skill','successful_trajectory_repair'] or object_hash(source)!=expected:
            raise ProtocolError('skill sources are missing, changed, or unsupported')
        sources.append(source)
    directory=Path(binding['audit_directory'])
    if sha(directory/'proposal.json')!=binding['proposal_sha256']:
        raise ProtocolError('curator proposal changed')
    if sha(directory/'input.json')!=binding['input_sha256']:
        raise ProtocolError('curator input changed')
    for name,expected in binding['model_artifact_sha256'].items():
        if sha(directory/name)!=expected:raise ProtocolError('curator model evidence changed')
    prompt=json.loads((directory/'input.json').read_text())
    if prompt['source_records']!=binding['source_records'] or prompt['protocol_sha256']!=protocol['protocol_sha256']:
        raise ProtocolError('curator source binding changed')
    model=list((directory/'context/model').glob('*/response.json'))
    if not model:raise ProtocolError('missing metered skill generation evidence')
    rebuilt=skill_record({k:record[k] for k in FIELDS},sources,audit_directory=directory,protocol=protocol,parent=binding['parent'])
    if rebuilt!=record:raise ProtocolError('skill record lost its evidence binding')


CURATOR_SYSTEM = (
    'Extract one concise reusable PLC ST implementation skill from the supplied verified TRAINING programs. '
    'A skill is a conditional procedure, not a task answer and not a claim that unseen code is correct. '
    'Use only mechanisms visible in BOTH source examples. State when the skill must not be applied. '
    'Do not invent successful checks, performance gains, or causal explanations. '
    'Return precisely five string fields: trigger, preconditions, procedure, verification, boundary. '
    'Keep the complete JSON under 1000 characters. Use concise English and short ST expressions where useful. '
    'Do not copy full source programs or task identities into the five strings.'
)
