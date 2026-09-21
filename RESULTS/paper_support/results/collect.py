"""Read selected, hash-verified records from the six local evidence archives."""
from pathlib import Path
import hashlib
import json
import re
import tarfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / 'evidence'

def wanted(name, host):
    common = [
        r'evidence/source/baseline_common/workflow.py',
        r'evidence/source/experiments/baseline8_online117_study/memory.py',
        r'evidence/source/experiments/baseline8_mixedfeedback200_study/[^/]+\.py',
        r'evidence/cost_accounting/(task_costs.json|call_costs.jsonl|candidate_costs.jsonl|calls.json)',
        r'evidence/tests/[^/]+/[^/]+/summary.json',
    ]
    specific = ([
        r'evidence/tests/[^/]+/[^/]+/generation/result.json',
        r'evidence/tests/[^/]+/[^/]+/cost.json',
        r'evidence/tests/[^/]+/[^/]+/cost_calls.json',
        r'evidence/tests/[^/]+/TR_C07_C10_06/generation/(task.json|model/0001/request.json)',
        r'evidence/streams/[^/]+/snapshots/0100/(state.json|snapshot.json|[^/]+.json)',
    ] if host == 'nas' else [
        r'evidence/arms/[^/]+/runs/[0-9]+/(result.json|publication.json|retained_prior_learning_cost.json)',
        r'evidence/arms/[^/]+/runs/0001/(task.json|model/0001/request.json)',
    ])
    return any(re.fullmatch(pattern,name) for pattern in common+specific)

def main():
    counts = {}
    for host in ('nas','huashuo'):
        for model in ('qwen','deepseek','haiku'):
            src=ROOT/'RESULTS'/host/model
            manifest={r['path']:r for line in (src/'MANIFEST.jsonl').read_text().splitlines()
                      for r in [json.loads(line)]}
            hashes={}
            with tarfile.open(src/'process_evidence.tar.gz','r|gz') as archive:
                for member in archive:
                    if not member.isfile() or not wanted(member.name,host):
                        continue
                    raw=archive.extractfile(member).read()
                    digest=hashlib.sha256(raw).hexdigest()
                    assert digest==manifest[member.name]['sha256'],member.name
                    dest=OUT/host/model/member.name.removeprefix('evidence/')
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    if not dest.exists(): dest.write_bytes(raw)
                    else: assert hashlib.sha256(dest.read_bytes()).hexdigest()==digest
                    hashes[member.name]=digest
            counts[f'{host}/{model}']=len(hashes)
            (OUT/host/model/'selected_manifest.json').write_text(json.dumps(hashes,indent=2)+'\n')
            print(host,model,len(hashes),'files verified',flush=True)
    (OUT.parent/'collection.json').write_text(json.dumps(counts,indent=2)+'\n')

if __name__=='__main__': main()
