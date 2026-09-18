"""Start the verified Huashuo model without network downloads or context truncation."""
import json
import os
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parent
    config = json.loads((root / 'config.json').read_text())
    proof = json.loads((root / 'model_verification.json').read_text())
    assert proof['sha256'] == config['model_sha256']
    assert Path(config['model_file']).stat().st_size == config['model_bytes']
    apps = subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid',
                                    '--format=csv,noheader'], text=True).strip()
    if apps:
        raise RuntimeError('Another compute process occupies the GPU; do not evict it')
    args = [config['engine'], '--model', config['model_file'], '--alias', config['model_id'],
            '--host', config['host'], '--port', str(config['port']), '--n-gpu-layers', '99',
            '--fit', 'off', '--ctx-size', str(config['shared_context_tokens']),
            '--parallel', str(config['parallel_slots']), '--kv-unified',
            '--kv-unified-per-slot', str(config['slot_context_limit']),
            '--cache-type-k', config['kv_cache_type'], '--cache-type-v', config['kv_cache_type'],
            '--flash-attn', 'on', '--batch-size', str(config['batch_size']),
            '--ubatch-size', str(config['ubatch_size']), '--threads', '8', '--threads-batch', '8',
            '--ctx-checkpoints', '4', '--cache-ram', '1024', '--jinja', '--reasoning', 'off',
            '--n-predict', str(config['default_max_output_tokens']), '--timeout', '600',
            '--no-context-shift', '--metrics', '--slots', '--offline', '--log-timestamps']
    env = {**os.environ, 'LD_LIBRARY_PATH': str(root / 'runtime/app') + ':' + str(root / 'runtime/cuda')}
    os.execve(args[0], args, env)


if __name__ == '__main__':
    main()
