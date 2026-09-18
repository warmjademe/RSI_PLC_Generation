"""Complete startup and probes after the verified model download finishes."""
import json
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path('/home/qyb/qwen38-27b-quantized')


def state(phase):
    p = ROOT / 'ops/bootstrap_state.json'
    p.write_text(json.dumps({'phase': phase, 'epoch': time.time()}) + '\n')


def main():
    state('waiting_for_verified_model')
    for _ in range(720):
        if (ROOT / 'STOP').exists():
            state('stopped'); return
        p = ROOT / 'ops/download_state.json'
        if p.exists() and json.loads(p.read_text()).get('phase') == 'complete':
            break
        time.sleep(5)
    else:
        raise TimeoutError('verified model not ready within one hour')
    state('starting_service')
    subprocess.run(['/usr/bin/python3', '-B', str(ROOT / 'ops/install_service.py'), '--start'], check=True)
    for _ in range(90):
        if (ROOT / 'STOP').exists():
            subprocess.run(['systemctl', '--user', 'stop', 'nas-qwen38-27b-q4km.service'], check=True)
            state('stopped'); return
        try:
            with urllib.request.urlopen('http://127.0.0.1:18185/health', timeout=3) as response:
                if response.status == 200:
                    break
        except Exception:
            pass
        time.sleep(2)
    else:
        state('health_check_failed')
        raise TimeoutError('service did not become healthy')
    state('verifying')
    subprocess.run(['/usr/bin/python3', '-u', '-B', str(ROOT / 'ops/verify_service.py')], check=True)
    state('verified')


if __name__ == '__main__':
    main()
