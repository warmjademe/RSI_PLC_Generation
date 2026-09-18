"""Isolated API, streaming, concurrency, and cancellation deployment probes."""
import concurrent.futures
import json
from pathlib import Path
import statistics
import subprocess
import threading
import time
import urllib.request

ROOT = Path('/home/qyb/qwen38-27b-quantized')
BASE = 'http://127.0.0.1:18185'
MODEL = 'qwen3.8-27b-q4_k_m'
OUT = ROOT / 'evidence' / time.strftime('verification_%Y%m%d_%H%M%S')


def save(name, value):
    (OUT / (name + '.json')).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return json.load(response)


def stream(name, prompt, limit=256, barrier=None, cancel=False, json_mode=False):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': limit, 'temperature': 0, 'seed': 20260915,
            'stream': True, 'stream_options': {'include_usage': True},
            'chat_template_kwargs': {'enable_thinking': False}}
    if json_mode:
        body['response_format'] = {'type': 'json_object'}
    save(name + '_request', body)
    request = urllib.request.Request(BASE + '/v1/chat/completions', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    if barrier:
        barrier.wait(timeout=15)
    start = time.monotonic()
    first = None
    text = ''
    reasoning = ''
    events = []
    finish = None
    usage = None
    done = False
    with urllib.request.urlopen(request, timeout=120) as response:
        for raw in response:
            if time.monotonic() - start > 180:
                raise TimeoutError('deployment probe exceeded 180 seconds')
            line = raw.decode().strip()
            if not line.startswith('data:'):
                continue
            value = line[5:].strip()
            if value == '[DONE]':
                done = True
                break
            event = json.loads(value)
            events.append(event)
            if event.get('usage'):
                usage = event['usage']
            for choice in event.get('choices', []):
                delta = choice.get('delta', {})
                part = delta.get('content') or ''
                reasoning += delta.get('reasoning_content') or ''
                if part.strip() and first is None:
                    first = time.monotonic() - start
                text += part
                finish = choice.get('finish_reason') or finish
            if cancel and len(text.strip()) >= 32:
                break
    elapsed = time.monotonic() - start
    result = {'elapsed_seconds': elapsed, 'first_content_seconds': first,
              'content': text, 'reasoning_content': reasoning, 'usage': usage,
              'finish_reason': finish, 'done_marker': done, 'intentionally_cancelled': cancel,
              'output_tokens_per_second_end_to_end': usage['completion_tokens'] / elapsed if usage else None}
    save(name + '_events', events)
    save(name + '_result', result)
    assert text.strip(), 'no usable text'
    assert not reasoning.strip() and '<think>' not in text, 'thinking unexpectedly enabled'
    if not cancel:
        assert done and usage and usage['completion_tokens'] > 0, 'incomplete stream or missing usage'
    return result


def main():
    OUT.mkdir(parents=True)
    listing = get('/v1/models')
    assert [x['id'] for x in listing['data']] == [MODEL]
    save('model_listing', listing)
    save('health', get('/health'))
    save('properties', get('/props'))
    print('API and model identity OK', flush=True)
    tiny = stream('json_smoke', 'Return exactly the JSON object {"ok":true}.', 64, json_mode=True)
    assert json.loads(tiny['content']) == {'ok': True}
    plc = stream('plc_smoke', 'Return a JSON object with one string field code. Write IEC 61131-3 '
        'Structured Text FUNCTION_BLOCK FB_StartStop with BOOL inputs StartButton and StopButton and '
        'BOOL output Motor. Stop has priority. Start latches Motor TRUE; otherwise retain Motor. '
        'Return the complete function block; no Markdown.', 512, json_mode=True)
    code = json.loads(plc['content'])['code']
    assert 'FUNCTION_BLOCK' in code and 'END_FUNCTION_BLOCK' in code
    (OUT / 'plc_smoke.st').write_text(code + '\n')
    print('JSON and ST generation OK', flush=True)
    prompt = 'Write a detailed numbered checklist for offline verification of PLC Structured Text programs. '
    single = stream('throughput_single', prompt + 'Include at least 40 detailed items.', 256)
    barrier = threading.Barrier(8)
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(stream, f'throughput_parallel_{i}',
                    prompt + f'Checklist version {i}: include at least 40 detailed items.', 256, barrier)
                   for i in range(8)]
        parallel = [future.result() for future in futures]
    wall = time.monotonic() - started
    print('8 concurrent streams completed with usage', flush=True)
    stream('cancel_smoke', prompt + 'Include at least 200 detailed items.', 8192, cancel=True)
    slots = []
    for _ in range(50):
        slots = get('/slots')
        if not any(s.get('is_processing') for s in slots):
            break
        time.sleep(.2)
    else:
        raise RuntimeError('cancelled request retained a busy slot')
    save('slots_after_cancellation', slots)
    gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,utilization.gpu',
                                    '--format=csv,noheader'], text=True).strip()
    study = Path('/home/qyb/RESEARCH/RSI_PLC_Generation/baseline8_mixedfeedback_qwen200_20260915_v2')
    assert (study / 'STOP').exists()
    unit = 'plc-baseline8-qwen200-watchdog-20260915-v2.timer'
    assert subprocess.run(['systemctl', '--user', 'is-active', unit], capture_output=True).returncode != 0
    result = {'status': 'pass', 'epoch': time.time(), 'model': MODEL, 'base_url': BASE + '/v1',
        'single_stream_output_tokens_per_second': single['output_tokens_per_second_end_to_end'],
        'parallel_clients': 8, 'parallel_wall_seconds': wall,
        'parallel_aggregate_output_tokens_per_second': sum(p['usage']['completion_tokens'] for p in parallel) / wall,
        'parallel_median_client_output_tokens_per_second': statistics.median(p['output_tokens_per_second_end_to_end'] for p in parallel),
        'parallel_first_content_seconds': [p['first_content_seconds'] for p in parallel],
        'cancelled_request_slot_released': True, 'gpu': gpu,
        'baseline_experiment_remains_stopped': True,
        'scope': 'short synthetic text probes; no PLC benchmark accuracy or full-context capacity claim',
        'evidence_directory': str(OUT)}
    save('summary', result)
    (ROOT / 'evidence/latest_verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
