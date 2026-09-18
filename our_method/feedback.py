"""Normalize public tool failures without consuming independent test verdicts."""
import json
import re


ALIASES = {'compile':'compiler', 'formal':'plcverif', 'runtime':'openplc_feedback'}


def failure_stages(feedback):
    stages = set()
    for item in feedback:
        if item.get('status') != 'fail':
            continue
        stage = item.get('stage', item.get('name'))
        if stage:
            stages.add(stage)
            if stage in ALIASES:
                stages.add(ALIASES[stage])
        evidence = item.get('evidence', {})
        # Command validators wrap the original report. Do not mistake the
        # generic "specification" stage for a particular verification backend.
        for _ in range(4):
            if not isinstance(evidence, dict):
                break
            gates = evidence.get('failed_gates', [])
            if isinstance(gates, list):
                stages.update(g for g in gates if isinstance(g, str))
            evidence = evidence.get('tool_report')
    return stages


def excerpt(value, maximum):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= maximum else text[:maximum-24] + ' [excerpt; full archived]'


def diagnostic_brief(feedback, maximum=1000):
    result = []
    for item in feedback:
        if item.get('status') != 'fail':
            continue
        diagnostics = item.get('diagnostics', item.get('summary', []))
        if not isinstance(diagnostics, list):
            diagnostics = [diagnostics]
        diagnostics = [entry for raw in diagnostics for entry in (
            raw['assertions'] if isinstance(raw, dict) and raw.get('kind') == 'runtime_counterexample'
            and isinstance(raw.get('assertions'), list) else [raw])]
        for raw in diagnostics:
            if isinstance(raw, dict):
                keys = ['variable','operator','expected','observed','type','case','step','kind','message','summary']
                raw = {k:raw[k] for k in keys if k in raw}
            result.append({'stage':item.get('stage', item.get('name')), 'diagnostic':excerpt(raw,400)})
            if len(result) >= 4:
                break
        if len(result) >= 4:
            break
    while result and len(json.dumps(result,ensure_ascii=False)) > maximum:
        result.pop()
    return result


def failure_query(feedback):
    return ' '.join(sorted(failure_stages(feedback))) + ' ' + json.dumps(diagnostic_brief(feedback),ensure_ascii=False)


def mentioned_variables(feedback):
    return set(re.findall(r'\b(?:A_|B_)[A-Za-z][A-Za-z_0-9]*\b', failure_query(feedback)))
