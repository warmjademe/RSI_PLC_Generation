"""Stream a selective, hash-bound evidence archive from one completed run.

Run remotely with --root. Standard output is a gzip tar stream, never logging.
No source files are modified. Symlinks within the experiment project are read
as their target contents; links outside it are rejected.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import time

TEXT = {'.json', '.jsonl', '.md', '.txt', '.log', '.st', '.csv', '.py', '.yaml', '.toml'}
SECRET = re.compile(rb'(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|olp_[A-Za-z0-9]{20,}|-----BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY-----|https://api\.day\.app/[A-Za-z0-9]{12,})')


def select(rel, path):
    parts = rel.parts
    if any(x in ('__pycache__', '.git', '.venv', 'cache', 'rejected_learning') for x in parts): return False
    if len(parts) == 1: return path.suffix in TEXT
    if parts[0] in ('dataset', 'public', 'public_dataset', 'source', 'evaluator_source', 'execution_source'):
        return path.suffix in TEXT
    if parts[0] == 'cost_accounting': return path.suffix in TEXT
    if parts[0] in ('streams', 'extension_learning'):
        if path.name in ('snapshot.json', 'learning_audit.json', 'native_learning.json', 'controller_selection.json', 'formation_calls.json'): return True
        # Final state of each original stream; intermediate state duplication
        # is omitted. Actual retrieved contexts remain in per-call requests.
        return 'snapshots' in parts and '0100' in parts and path.suffix in ('.json', '.jsonl')
    if parts[0] not in ('tests', 'arms', 'incidents', 'qualification', 'bootstrap'): return False
    if 'workspace' in parts or 'interrupted_workspace' in parts:
        return path.name in ('authorized_request.json', 'authorized_result.json', 'trace.json')
    if 'wire' in parts:
        if path.name == 'response.sse':
            return not (path.parent / 'response.json').exists()  # Retain failed/incomplete raw streams.
        return path.name not in ('stream_progress.json', 'admission.json') and path.suffix in TEXT
    return path.suffix in TEXT


def add(tar, name, data):
    info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def main():
    p = argparse.ArgumentParser(); p.add_argument('--root', type=Path, required=True)
    root = p.parse_args().root.resolve(); project = root.parent
    if not any((root / n).exists() for n in ('completion_audit.json', 'extension_completion.json')):
        raise RuntimeError('completed run audit required')
    records = []; omissions = Counter(); bytes_out = 0; started = time.time(); links = {}
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz', compresslevel=6) as tar:
        for directory, dirs, files in os.walk(root, followlinks=True):
            dirs[:] = sorted(d for d in dirs if d not in ('__pycache__', '.git', '.venv', 'cache'))
            for name in dirs + files:
                path = Path(directory) / name
                if path.is_symlink():
                    target = path.resolve()
                    if not target.is_relative_to(project): raise RuntimeError('external symlink outside experiment project')
                    links[str(path.relative_to(root))] = str(target.relative_to(project))
            for name in sorted(files):
                path = Path(directory) / name; rel = path.relative_to(root)
                if not select(rel, path):
                    omissions[rel.parts[0]] += 1; continue
                if not path.is_file(): continue
                data = path.read_bytes()
                if SECRET.search(data):
                    raise RuntimeError('credential pattern detected in selected evidence: ' + str(rel))
                add(tar, 'evidence/' + str(rel), data)
                records.append({'path': 'evidence/' + str(rel), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
                bytes_out += len(data)
        manifest = ''.join(json.dumps(x, ensure_ascii=False, sort_keys=True)+'\n' for x in records).encode()
        add(tar, 'MANIFEST.jsonl', manifest)
        report = {'source_run': root.name, 'started_epoch': started, 'completed_epoch': time.time(),
            'files': len(records), 'uncompressed_evidence_bytes': bytes_out,
            'manifest_sha256': hashlib.sha256(manifest).hexdigest(),
            'excluded_file_counts_by_top_directory': dict(omissions), 'dereferenced_symlinks': links,
            'secret_pattern_scan': 'pass',
            'scope': 'Selected process evidence, not a byte-for-byte remote backup. Includes input data, frozen source, requests/responses, candidate/feedback/check receipts, authorized evaluation results and traces, cost ledgers, learning audits and final original-stream text state.',
            'omitted': 'Compiler binaries/intermediates, caches, repetitive intermediate knowledge states, vector arrays/controller weights/databases, monitor polling; successful SSE is omitted when response.json exists. Incomplete SSE is retained.',
            'portability': 'Original absolute paths are preserved as provenance; hashes in frozen protocol may refer to intentionally omitted files.'}
        add(tar, 'EXPORT.json', json.dumps(report, ensure_ascii=False, indent=2).encode()+b'\n')


if __name__ == '__main__': main()
