"""Evidence-bound, bounded repair excerpts; complete raw records remain frozen."""
import difflib
import hashlib
import json
import re

from .feedback import diagnostic_brief, excerpt, mentioned_variables


def code_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def change_hunks(before, after):
    chunks, current = [], []
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), n=2, lineterm=''):
        if line.startswith(('---','+++')):
            continue
        if line.startswith('@@') and current:
            chunks.append(current);current=[]
        current.append(line)
    if current:
        chunks.append(current)
    return chunks


def compact_repair(record, maximum, feedback=()):
    if maximum < 1200:
        return None
    before, after = record['before_code'], record['after_code']
    result = {k:record[k] for k in ['id','kind','trigger_stages','trajectory_terminal_success','scope']}
    result.update(source_task_id=record['task_id'],
                  observed_failure=diagnostic_brief(record['observed_failure'],650),
                  repair_hypothesis=excerpt(record.get('repair_hypothesis',''),300),
                  after_gate_statuses=[{'name':g.get('name'), 'status':g.get('status')}
                                       for g in record.get('after_feedback',[])],
                  before_sha256=code_hash(before), after_sha256=code_hash(after),
                  change_format='unified_diff_excerpts; not a standalone program or automatically applicable patch',
                  change_excerpts=[], omitted_hunks=0)
    # Prefer changes involving the observed variables, then source order.
    names = mentioned_variables(feedback) | mentioned_variables(record['observed_failure'])
    hunks = change_hunks(before,after)
    ordered = sorted(enumerate(hunks),key=lambda pair:(-sum(n in '\n'.join(pair[1]) for n in names),pair[0]))
    retained = 0
    for _,lines in ordered:
        text = '\n'.join(lines)
        proposed = {**result,'change_excerpts':result['change_excerpts']+[text]}
        if len(json.dumps(proposed,ensure_ascii=False)) <= maximum:
            result=proposed;retained+=1;continue
        # A large rewrite may form one huge hunk. Keep whole changed lines with
        # an explicit omission marker; never imply that this is the full patch.
        changes = [line for line in lines if line.startswith(('+','-'))]
        focus = [line for line in changes if any(name in line for name in names)]
        candidates = focus or changes
        snippet = (lines[0] if lines and lines[0].startswith('@@') else '') + '\n[HUNK EXCERPT; intervening lines may be omitted]'
        added = 0
        for line in candidates:
            candidate = {**result,'change_excerpts':result['change_excerpts']+[snippet+'\n'+line]}
            if len(json.dumps(candidate,ensure_ascii=False)) <= maximum:
                snippet+='\n'+line;added+=1
        if added:
            result['change_excerpts'].append(snippet);retained+=1
        if len(result['change_excerpts']) >= 3:
            break
    result['omitted_hunks'] = len(hunks)-retained
    # No change evidence means this record cannot provide a usable repair hint.
    if not result['change_excerpts'] or len(json.dumps(result,ensure_ascii=False)) > maximum:
        return None
    return result


def compact_skill(record, maximum):
    from baseline_common.utils import object_hash
    keys = ['id','kind','trigger','preconditions','procedure','verification','boundary']
    item = {k:record[k] for k in keys}
    item.update(evidence_task_ids_excerpt=record['evidence_task_ids'][:4],
                evidence_task_count=len(record['evidence_task_ids']),
                evidence_binding_sha256=object_hash(record.get('source_binding',{})))
    return item if len(json.dumps(item,ensure_ascii=False)) <= maximum else None
