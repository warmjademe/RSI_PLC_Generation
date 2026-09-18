"""Run the pinned local GPU service, refusing to evict any other GPU user."""
import json
import os
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parent.parent
    cfg = json.loads((root / 'ops/config.json').read_text())
    model = root / 'models' / cfg['model_file']
    proof = json.loads((root / 'ops/download_state.json').read_text())
    if (proof.get('phase') != 'complete' or proof.get('verified_sha256') != cfg['model_sha256']
            or model.stat().st_size != cfg['model_bytes']):
        raise ValueError('verified model artifact required')
    if (root / 'STOP').exists():
        raise RuntimeError('deployment STOP marker present')
    runtime = json.loads((root / 'ops/native_runtime_provenance.json').read_text())
    if runtime['image_manifest'] != cfg['image'].split('@')[1]:
        raise ValueError('runtime provenance does not match pinned image')
    apps = subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid,process_name,used_gpu_memory',
                                    '--format=csv,noheader'], text=True).strip()
    if apps:
        raise RuntimeError('GPU occupied by another compute process; existing workloads left running')
    slots = root / 'runtime/slot-cache'
    slots.mkdir(exist_ok=True)
    args = [str(root / 'runtime/app/llama-server'), '--model', str(model), '--alias', cfg['model_id'],
        '--host', cfg['host'], '--port', str(cfg['port']), '--n-gpu-layers', '999', '--fit', 'off',
        '--ctx-size', str(cfg['shared_context_tokens']), '--parallel', str(cfg['parallel_slots']),
        '--kv-unified', '--kv-unified-per-slot', str(cfg['slot_context_limit']),
        '--cache-type-k', cfg['kv_cache_type'], '--cache-type-v', cfg['kv_cache_type'],
        '--flash-attn', 'on', '--batch-size', str(cfg['batch_size']), '--ubatch-size', str(cfg['ubatch_size']),
        '--ctx-checkpoints', '4', '--cache-ram', '2048', '--threads', '8',
        '--threads-batch', '8', '--jinja', '--reasoning', 'on' if cfg['enable_thinking'] else 'off',
        '--n-predict', str(cfg['default_max_output_tokens']), '--timeout', '600',
        '--no-context-shift', '--metrics', '--slots', '--slot-save-path', str(slots), '--no-ui',
        '--cors-origins', f"http://127.0.0.1:{cfg['port']},http://localhost:{cfg['port']}"]
    cache = root / 'runtime/cuda-cache'
    cache.mkdir(exist_ok=True)
    env = {**os.environ, 'CUDA_VISIBLE_DEVICES': '0', 'CUDA_CACHE_PATH': str(cache),
           'LD_LIBRARY_PATH': str(root / 'runtime/app') + ':' + str(root / 'runtime/cuda')}
    os.execve(args[0], args, env)


if __name__ == '__main__':
    main()
