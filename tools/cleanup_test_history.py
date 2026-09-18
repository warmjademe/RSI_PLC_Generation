"""Reversibly archive obsolete local data; retain the 100-task experiment.

No remote host is touched. Legacy source modules are deliberately retained:
the current runners still import them. Run without --apply to inspect the plan.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_ARTIFACTS = '''baseline8_online117_20260914 baseline8_protocol_review_20260915
history_feedback200_full_twolevel_20260915 speed_diagnosis_20260916
history_feedback200_watchdog_20260915 repair_policy117_20260914
baseline8_online200_watchdog_20260915 qwen38q4_monitor_v4_20260915
history_feedback200_ablation_twolevel_20260916 qwen38q4_max10_restart_20260915
historical_assets_candidates history_feedback200_costs_20260915
baseline8_mixedfeedback200_20260915 training1000_scope_20260915
qwen38q4_restart_20260915 status_20260916_1400 history_feedback117_20260914
baseline8_online200_20260915 output8k_v5_20260916
history_feedback200_launch_20260915 history_feedback200_local_q4_20260915
history_feedback200_local_q4_c10_20260915 concurrency_diagnosis_20260916
baseline8_speed_watchdog_20260915 test_dataset_200_preparation_20260915'''.split()


def inventory(path):
    files = [path] if path.is_file() or path.is_symlink() else sorted(path.rglob('*'))
    result = {}
    for p in files:
        name = str(p.relative_to(path)) if p != path else '.'
        if p.is_symlink(): result[name] = {'symlink': str(p.readlink())}
        elif p.is_file():
            result[name] = {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    return result


def targets():
    paths = [ROOT / n for n in ('test_dataset_200_tasks', 'test_datset_117_tasks',
        'final_train_datasets', 'training_datasets', 'CLEANUP_REPORT.json',
        'DELIVERY_CREDENTIAL_SCAN.json', 'DELIVERY_TESTS.log', 'DELIVERY_VALIDATION.json')]
    paths += [ROOT / 'artifacts' / n for n in OLD_ARTIFACTS]
    paths += [ROOT / 'experiments' / n / 'results_20260912' for n in ('mixed117_study', 'hard45_study', 'ablation45_study')]
    paths += [ROOT / 'experiments/historical_assets_study' / n for n in ('results', 'diagnostics', 'runtime_observations', 'deployments')]
    for folder in ('deepseek_study', 'rsi_study', 'historical_assets_study', 'mixed117_study'):
        paths += [p for p in (ROOT / 'experiments' / folder).iterdir()
                  if p.is_file() and (p.name.isupper() or p.stem.isupper()) and p.suffix in ('.json', '.csv')]
    paths = [p for p in paths if p.exists()]
    for p in sorted(ROOT.rglob('__pycache__')):
        if not any(parent == p or parent in p.parents for parent in paths): paths.append(p)
    for p in sorted(ROOT.rglob('.DS_Store')):
        if not any(parent == p or parent in p.parents for parent in paths): paths.append(p)
    return paths


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(); selected = targets()
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    archive = ROOT.parent / 'source_codes_archive' / ('pre_100_only_' + stamp)
    if not args.apply:
        print(json.dumps({'archive': str(archive), 'targets': [str(p.relative_to(ROOT)) for p in selected]}, indent=2)); return
    retained = {str(p.relative_to(ROOT)): inventory(p) for p in [ROOT / 'test_dataset_100_tasks',
        *[p for p in (ROOT / 'artifacts').iterdir() if p not in selected]]}
    archive.mkdir(parents=True, mode=0o700)
    report = {'archive': str(archive), 'action': 'reversible_same_filesystem_move',
              'remote_hosts_modified': False, 'moves': [], 'status': 'in_progress'}
    def save():
        (archive / 'cleanup_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    save()
    for p in selected:
        rel = p.relative_to(ROOT); dest = archive / rel; before = inventory(p)
        dest.parent.mkdir(parents=True, exist_ok=True)
        p.rename(dest)
        assert inventory(dest) == before, str(rel)
        report['moves'].append({'path': str(rel), 'files': before,
            'bytes': sum(v.get('bytes', 0) for v in before.values())})
        save()
    for rel, before in retained.items():
        after = inventory(ROOT / rel)
        # Bytecode caches are not scientific evidence and were archived separately.
        before = {k:v for k,v in before.items() if '__pycache__' not in Path(k).parts and Path(k).name != '.DS_Store'}
        assert after == before, 'retained data changed: ' + rel
    report.update(status='pass', retained_data_unchanged=True,
        archived_bytes=sum(m['bytes'] for m in report['moves']))
    save()
    public = {k:v for k,v in report.items() if k not in ('archive', 'moves')}
    public['archive_location'] = '../source_codes_archive/' + archive.name
    public['moved_paths'] = [m['path'] for m in report['moves']]
    public['retained_artifact_directories'] = [k for k in retained if k.startswith('artifacts/')]
    (ROOT / 'CLEANUP_100_ONLY.json').write_text(json.dumps(public, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in public.items() if k not in ('moved_paths', 'retained_artifact_directories')}, indent=2))


if __name__ == '__main__': main()
