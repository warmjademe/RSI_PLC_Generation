"""Install this one inference service without touching existing experiments."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    cfg = json.loads((root / 'ops/config.json').read_text())
    unit = Path.home() / '.config/systemd/user' / cfg['unit']
    unit.parent.mkdir(parents=True, exist_ok=True)
    content = f'''[Unit]
Description=Qwen3.8-27B Q4_K_M local GPU inference
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=exec
WorkingDirectory={root}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 -u -B {root}/ops/serve.py
KillMode=control-group
MemoryMax=40G
TimeoutStopSec=30
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
'''
    if unit.exists() and unit.read_text() != content:
        raise RuntimeError('unit already exists with different configuration')
    unit.write_text(content)
    subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)], check=True, capture_output=True)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', cfg['unit']], check=True)
    if '--start' in sys.argv:
        subprocess.run(['systemctl', '--user', 'start', cfg['unit']], check=True)
    print('Service installed' + (' and start requested' if '--start' in sys.argv else '; not started yet'))


if __name__ == '__main__':
    main()
