"""Collect six selective process-evidence packages; no remote writes or API calls."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'RESULTS'
BASE = '/home/qyb/RESEARCH/RSI_PLC_Generation/'
RUNS = [
    ('nas', 2222, 'qwen', 'baseline8_qwen38q4_100_max10_20260916_v1'),
    ('huashuo', 3333, 'qwen', 'history_feedback100_twolevel_20260916_v1'),
    ('nas', 2222, 'deepseek', 'baseline8_deepseek_v41_official_100_max10_20260917_v1'),
    ('huashuo', 3333, 'deepseek', 'history_feedback100_deepseek_final_20260917'),
    ('nas', 2222, 'haiku', 'baseline8_haiku45_100_extend10_20260917_v1'),
    ('huashuo', 3333, 'haiku', 'history_feedback100_haiku_twolevel_20260917_v3'),
]


def verify(path, dest):
    actual = {}; summaries = {}
    with tarfile.open(path, 'r|gz') as tar:
        for member in tar:
            if not member.isfile() or member.name.startswith('/') or '..' in Path(member.name).parts:
                raise ValueError('unsafe archive member')
            data = tar.extractfile(member).read()
            if member.name == 'MANIFEST.jsonl': manifest = data
            elif member.name == 'EXPORT.json': report = json.loads(data)
            else:
                if member.name in actual: raise ValueError('duplicate archive member')
                actual[member.name] = {'path': member.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                rel = Path(member.name).relative_to('evidence')
                if (len(rel.parts) == 1 and rel.suffix in ('.json', '.md')) or str(rel) in ('cost_accounting/report.json', 'cost_accounting/summary.json'):
                    summaries[str(rel)] = data
    expected = {x['path']:x for x in map(json.loads, manifest.splitlines())}
    assert actual == expected and len(actual) == report['files']
    assert hashlib.sha256(manifest).hexdigest() == report['manifest_sha256']
    for name, data in summaries.items():
        p = dest / 'summary' / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    (dest / 'MANIFEST.jsonl').write_bytes(manifest)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(4*1024*1024), b''): digest.update(chunk)
    report.update(archive_bytes=path.stat().st_size, archive_sha256=digest.hexdigest(), local_verification='all_file_hashes_pass')
    (dest / 'EXPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report


def collect(item):
    host, port, model, run = item
    dest = OUT / host / model; dest.mkdir(parents=True, exist_ok=True)
    archive = dest / 'process_evidence.tar.gz'
    if archive.exists():
        report = verify(archive, dest)
        return {'host':host, 'model':model, **report}
    partial = dest / 'process_evidence.tar.gz.partial'
    script = (ROOT / 'tools/export_process_evidence.py').read_bytes()
    cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=30',
           '-i', str(Path.home()/'.ssh/id_ed25519'), '-p', str(port), 'qyb@nas.qyb.name',
           'python3 - --root ' + BASE + run]
    print(json.dumps({'phase':'downloading','host':host,'model':model}), flush=True)
    with partial.open('wb') as output, (dest / 'transfer.log').open('wb') as log:
        done = subprocess.run(cmd, input=script, stdout=output, stderr=log)
    if done.returncode: raise RuntimeError(f'{host}/{model} export failed; see transfer.log')
    print(json.dumps({'phase':'verifying','host':host,'model':model,'archive_bytes':partial.stat().st_size}), flush=True)
    report = verify(partial, dest)
    partial.rename(archive)
    result = {'host':host,'model':model,**report}
    print(json.dumps({'phase':'verified','host':host,'model':model,'files':report['files'],'archive_bytes':report['archive_bytes']}), flush=True)
    return result


def main():
    OUT.mkdir(exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(collect, item) for item in RUNS]
        for future in as_completed(futures):
            results.append(future.result())
            (OUT / 'collection_progress.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    (OUT / 'INDEX.json').write_text(json.dumps({'status':'pass','runs':results,
        'total_archive_bytes':sum(x['archive_bytes'] for x in results),
        'total_evidence_files':sum(x['files'] for x in results)},ensure_ascii=False,indent=2)+'\n')
    print('All six evidence packages downloaded and hash verified.', flush=True)


if __name__ == '__main__': main()
