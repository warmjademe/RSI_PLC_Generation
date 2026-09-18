"""Admission reads tool receipts and metered generation artifacts, never model scores."""
import json
from pathlib import Path

from baseline_common.datasets import sha
from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash, object_hash
from .rsi_protocol import assert_training_sources, record_sources


def read_outcome(directory, *, role, version, protocol):
    directory = Path(directory).resolve()
    result = json.loads((directory/'generation/result.json').read_text())
    judge = json.loads((directory/'judge/judge.json').read_text())
    binding = json.loads((directory/'binding.json').read_text())
    tid = result['task_id']
    expected = {'role':role, 'version':version, 'task_id':tid,
                'protocol_sha256':protocol['protocol_sha256']}
    if any(binding.get(k) != v for k,v in expected.items()):
        raise ProtocolError('generation receipt belongs to another partition/version/protocol')
    if role == 'development':
        if tid not in protocol['development_ids']:raise ProtocolError('not a development task')
    elif role == 'practice':
        assert_training_sources([tid],protocol)
    elif role == 'test':
        if not tid.startswith('TE_'):raise ProtocolError('not a test task')
    else:raise ProtocolError('unsupported evaluation role')
    if result['code_hash'] != content_hash(result['code']) or judge.get('code_hash') != result['code_hash']:
        raise ProtocolError('judgement is not bound to the delivered code')
    checks = judge.get('checks',[])
    for c in checks:
        if c.get('code_hash') != result['code_hash']:
            raise ProtocolError('final check belongs to another candidate')
        if c['status']=='pass' and not c.get('evidence',{}).get('executed'):
            raise ProtocolError('passing check was not executed')
    success = len(checks)==3 and [c['stage'] for c in checks]==['compile','runtime','formal'] and all(c['status']=='pass' for c in checks)
    if success != (judge['status']=='pass') or success != judge['success']:
        raise ProtocolError('final success disagrees with independent checks')
    budget = result['budget']
    if not 0 <= budget['candidates'] <= protocol['max_candidates']:
        raise ProtocolError('candidate budget was exceeded')
    if any(type(budget[k]) is not int or budget[k]<0 for k in ['input_tokens','output_tokens','model_calls']):
        raise ProtocolError('invalid audited resource usage')
    documents = ['binding.json','generation/result.json','judge/judge.json']
    return {**expected,'status':judge['status'],'success':success,
            'tokens':budget['input_tokens']+budget['output_tokens'],
            'input_tokens':budget['input_tokens'],'output_tokens':budget['output_tokens'],
            'model_calls':budget['model_calls'],'candidates':budget['candidates'],
            'estimated_charge_calls':budget.get('estimated_charge_calls',0),
            'code_hash':result['code_hash'],'directory':str(directory),
            'receipt_sha256':object_hash({name:sha(directory/name) for name in documents}),
            'evaluation_binding_sha256':binding['evaluation_binding_sha256']}


def verify_outcome(row, protocol):
    actual = read_outcome(row['directory'],role=row['role'],version=row['version'],protocol=protocol)
    if actual != row:raise ProtocolError('outcome was changed or lost its source receipt')
    return actual


def verified_practice(row, protocol):
    verify_outcome(row,protocol)
    if row['role']!='practice' or row['status']!='pass':
        raise ProtocolError('RSI assets require passing training practice; development/test outcomes are ineligible')
    return json.loads((Path(row['directory'])/'generation/result.json').read_text())


def learning_context_sources(row, protocol):
    """Close over the actual retrieved assets used by every practice attempt."""
    from baseline_common.binding import verify_adapter
    from baseline_common.memory import load
    if row['role']!='practice':raise ProtocolError('only training practice can supply learning context')
    directory=Path(row['directory'])/'generation'
    config=json.loads((directory/'run_config.json').read_text())
    memory=Path(config['method_config']['memory_root'])
    manifest=verify_adapter(memory,'OurMethod')
    bank={r['id']:r for r in load(memory,'OurMethod')[1]}
    sources=set()
    for path in sorted((directory/'memory').glob('candidate-*.json')):
        for item in json.loads(path.read_text())['items']:
            if item['id'] not in bank:raise ProtocolError('practice context cites an unknown learned asset')
            sources.update(record_sources(bank[item['id']]))
    if sources:assert_training_sources(sources,protocol)
    return sorted(sources)
