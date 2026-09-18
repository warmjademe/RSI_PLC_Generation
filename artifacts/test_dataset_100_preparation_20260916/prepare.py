"""Select top 100 by verified reference lines within the supplied 200-task pool."""
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import sys

SOURCE = Path(__file__).resolve().parents[2]
PARENT = SOURCE / 'test_dataset_200_tasks'
TARGET = SOURCE / 'test_dataset_100_tasks'
sys.path.insert(0, str(SOURCE))
from baseline_common.config import load_task


def read(p):
    return json.loads(p.read_text())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def main():
    assert not TARGET.exists() or not any(TARGET.iterdir()), 'Refusing to overwrite a nonempty dataset'
    parent = read(PARENT/'selection.json')
    manifest = read(PARENT/'manifest.json')
    assert parent['task_count'] == manifest['task_count'] == len(parent['tasks']) == 200
    for name, digest in manifest['files'].items():
        assert sha(PARENT/name) == digest, name
    rows = []
    for t in parent['tasks']:
        ref = SOURCE/t['source_directory']/'reference.st'
        assert sha(ref) == t['reference_sha256'], t['task_id']
        lines = len(ref.read_text(encoding='utf-8').splitlines())
        assert lines == t['reference_total_lines'], t['task_id']
        rows.append({**t, 'parent_rank': t['rank'], 'reference_total_lines': lines})
    rows.sort(key=lambda x: (-x['reference_total_lines'], x['task_id']))
    chosen = [{**t, 'rank': i} for i, t in enumerate(rows[:100], 1)]
    assert len({t['task_id'] for t in chosen}) == 100
    TARGET.mkdir(exist_ok=True)
    totals = dict(source_test_cases=0, source_test_steps=0, normalized_test_steps=0,
                  scheduled_scans=0, scheduled_assertions=0)
    for t in chosen:
        for key in ['task_file', 'tests_file', 'evaluator_file']:
            dst = TARGET/t[key]; dst.parent.mkdir(exist_ok=True)
            shutil.copyfile(PARENT/t[key], dst)
            assert sha(dst) == manifest['files'][t[key]]
        task = load_task(TARGET/t['task_file'])
        suite = read(TARGET/t['tests_file'])
        oracle = read(TARGET/t['evaluator_file'])
        assert task['id'] == suite['task_id'] == oracle['id'] == t['task_id']
        assert task['files'] == {} and task['public_properties'] == []
        assert oracle['source_suite_sha256'] == sha(TARGET/t['tests_file'])
        assert task['interface']['scan_period_ms'] == suite['scan_period_ms'] == oracle['runtime_plan']['scan_period_ms'] == 100
        assert len(suite['cases']) == len(oracle['runtime_plan']['cases'])
        for source, case in zip(suite['cases'], oracle['runtime_plan']['cases']):
            assert source['id'] == case['id'] and source['fresh_instance'] is True
            scans = sum(x.get('repeat', 1) for x in source['steps'])
            assertions = sum(len(x['expect'])*(x.get('repeat', 1) if x.get('check','each')=='each' else 1) for x in source['steps'])
            assert sum(x['cycles'] for x in case['steps']) == scans
            assert sum(len(x['assertions']) for x in case['steps']) == assertions
            totals['source_test_cases'] += 1
            totals['source_test_steps'] += len(source['steps'])
            totals['normalized_test_steps'] += len(case['steps'])
            totals['scheduled_scans'] += scans
            totals['scheduled_assertions'] += assertions
    cutoff = chosen[-1]['reference_total_lines']
    selection = {**{k:v for k,v in parent.items() if k not in ['tasks','replaces','replacement_reason','reference_total_lines','reference_logic_lines','cutoff_ties']},
        'dataset_id':'plc_longest100_runtime_v1_20260916', 'task_count':100, 'source_task_count':200,
        'source_dataset':'test_dataset_200_tasks', 'source_split':'test_dataset_200_tasks',
        'original_source_split':'train', 'source_manifest_sha256':sha(PARENT/'manifest.json'),
        'source_selection_sha256':sha(PARENT/'selection.json'),
        'selection_rule':'Within the supplied 200 tasks: reference.st physical line count descending; original task ID ascending for ties; first 100',
        'replaces':'test_dataset_200_tasks', 'replacement_reason':'User requested the 100 longest reference programs from the existing 200-task pool.',
        'reference_total_lines':{'min':cutoff,'median':statistics.median(t['reference_total_lines'] for t in chosen),'max':chosen[0]['reference_total_lines']},
        'reference_logic_lines':{'min':min(t['reference_logic_lines'] for t in chosen),'median':statistics.median(t['reference_logic_lines'] for t in chosen),'max':max(t['reference_logic_lines'] for t in chosen)},
        'cutoff_ties':{'line_count':cutoff,'in_source':sum(t['reference_total_lines']==cutoff for t in rows),'selected':sum(t['reference_total_lines']==cutoff for t in chosen)},
        'first_excluded':{k:rows[100][k] for k in ['task_id','reference_total_lines']}, 'tasks':chosen}
    save(TARGET/'selection.json', selection)
    protocol = read(PARENT/'evaluation_protocol.json')
    protocol.update(dataset_id=selection['dataset_id'], execution_status='Dataset packaging performs no PLC executions; experiment status is recorded separately.')
    save(TARGET/'evaluation_protocol.json', protocol)
    checks = {'status':'pass','task_count':100,'parent_reference_hashes_and_lines_verified':200,
        'task_suite_plan_files_copied_byte_for_byte':300,'source_200_dataset_modified':False,
        'references_copied':0,'formal_properties_copied':0,'model_calls':0,'PLC_executions':0,
        'preparation_script_sha256':sha(Path(__file__)),**totals}
    save(TARGET/'preparation_report.json',checks)
    files={str(p.relative_to(TARGET)):sha(p) for p in sorted(TARGET.rglob('*')) if p.is_file()}
    save(TARGET/'manifest.json',{'dataset_id':selection['dataset_id'],'task_count':100,'files':files,
        'scope':'Tasks, suites, plans, selection, protocol and preparation; README and manifest excluded.'})
    for name,digest in manifest['files'].items():assert sha(PARENT/name)==digest
    assert len(list(TARGET.glob('tasks/*.json')))==len(list(TARGET.glob('test_cases/*.json')))==len(list(TARGET.glob('evaluator/*.json')))==100
    print(json.dumps({'selection':{k:selection[k] for k in ['task_count','reference_total_lines','cutoff_ties','first_excluded']},'checks':checks},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
