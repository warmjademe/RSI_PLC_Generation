"""Read original paid ledgers; reconstruct without issuing any model/tool call."""
import sys
import time
from pathlib import Path
from experiments.baseline8_haiku100.extend import restore, audit
from experiments.baseline8_haiku100.provider import GuardedQwenProvider
from experiments.baseline8_online117_study.common import read, save, validate
from experiments.baseline8_online200_study.run import public_task

root = Path(sys.argv[1])
cfg = read(root / 'config.json')
checked = 0
for entry in read(root / 'extension_protocol.json')['entries']:
    if entry['original_success']:
        continue
    d = root / 'tests' / entry['method'] / entry['task_id']
    origin = read(d / 'extension_origin.json')
    ctx = restore(root, d, public_task(root, entry['task_id']), cfg, entry['method'],
                  GuardedQwenProvider(cfg['provider'], d / 'wire'))
    current = ctx.budget.report()
    for key in ('candidates', 'model_calls', 'tool_calls', 'input_tokens', 'output_tokens', 'estimated_charge_calls'):
        assert current[key] == origin['budget'][key], (entry['method'], entry['task_id'], key, current[key], origin['budget'][key])
    assert ctx.generation_calls == origin['generation_calls']
    assert ctx.provider.calls == origin['budget']['model_calls']
    sessions = read(d / 'extension_sessions.json')
    sessions[-1].update(ended_epoch=time.time(), zero_call_preflight=True)
    save(d / 'extension_sessions.json', sessions)
    checked += 1
result = audit(root, full=True)
validate(Path(read(root / 'extension_protocol.json')['parent']))
save(root / 'extension_preflight.json', {'status': 'pass', 'reconstructed_tasks': checked,
    'paid_model_calls': 0, 'tool_calls': 0, 'parent_protocol_unchanged': True,
    'same_original_token_and_attempt_ledgers': True, 'audit': result})
print({'status': 'pass', 'reconstructed_tasks': checked, 'paid_model_calls': 0})
