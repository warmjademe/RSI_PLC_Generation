"""Subsystem requirement coverage plus outcome-gated repair retrieval."""
import json
import re
from baseline_common.memory import compatible, packet, ranked
from .feedback import failure_stages, failure_query
from .repair_memory import compact_repair, compact_skill
from .code_projection import without_comments
from baseline_common.utils import content_hash


def clauses(requirement):
    result = {"A": set(), "B": set()}
    for line in requirement.splitlines():
        match = re.search(r"[Ss]ubsystem ([AB]) shall satisfy:\s*(.*)", line)
        if match:
            result[match[1]].add(" ".join(match[2].split()))
    return result


def variables(meta):
    result = {}
    for side in ["inputs", "outputs"]:
        fields = meta.get("interface", {}).get(side, [])
        if isinstance(fields, dict):
            fields = [{"name": k, "type": v} for k, v in fields.items()]
        for field in fields:
            result[field["name"]] = (side, field["type"])
    return result


def contract_delta(task, donor):
    q = variables(task.get("metadata", {"interface": task["interface"]}))
    d = variables(donor.get("metadata", {}))
    qc, dc = clauses(task["requirement"]), clauses(donor["requirement"])
    return {"query_only_variables": sorted(q.keys() - d.keys()), "donor_only_variables": sorted(d.keys() - q.keys()),
            "role_or_type_changes": {k: {"query": q[k], "donor": d[k]} for k in q.keys() & d.keys() if q[k] != d[k]},
            "unmatched_query_clauses": {s: sorted(qc[s] - dc[s]) for s in ["A", "B"]},
            "donor_only_clauses": {s: sorted(dc[s] - qc[s]) for s in ["A", "B"]},
            "query_only_assumptions": sorted(set(task.get('metadata',{}).get('assumptions',[]))-set(donor.get('metadata',{}).get('assumptions',[]))),
            "donor_only_assumptions": sorted(set(donor.get('metadata',{}).get('assumptions',[]))-set(task.get('metadata',{}).get('assumptions',[])))}


def retrieve(bank, task, feedback, config, *, audit=None):
    eligible = compatible(bank, task)
    maximum = int(config.get("memory_characters", 24000))
    selected = []
    code_budget = min(int(config.get("code_memory_characters", 14000)), maximum-128)
    audit = audit if audit is not None else {}
    audit.update(eligible_records=len(eligible), failure_stages=sorted(failure_stages(feedback)),
                 matching_repairs=0, repair_projections_rejected=0, selected_ids=[])
    if config.get("use_code_memory", True):
        donors = [r for r in eligible if r["kind"] == "verified_program"]
        lexical = ranked(donors, task, max(1, len(donors))) if donors else []
        order = {r["id"]: i for i, r in enumerate(lexical)}
        query = clauses(task["requirement"])
        covered = {"A": set(), "B": set()}
        donor_clauses = {r["id"]: clauses(r["requirement"]) for r in donors}
        while donors and len(selected) < int(config.get("program_top_k", 2)):
            def score(r):
                c = donor_clauses[r["id"]]
                return (sum(len((query[s] - covered[s]) & c[s]) for s in ["A", "B"])
                        if config.get('use_requirement_coverage',True) else 0,
                        -order.get(r["id"], len(donors)))
            ordered = sorted(donors, key=score, reverse=True)
            rejected = set()
            accepted = False
            for donor in ordered:
                rejected.add(donor["id"])
                item = {k: donor[k] for k in ["id", "target", "requirement", "interface", "code", "candidate_sha256"]}
                item["kind"] = "verified_contract_donor"
                if config.get('program_representation') == 'code_with_contract_delta':
                    # Keep the complete executable program. The current public
                    # contract and explicit delta replace duplicate donor prose.
                    item.pop('requirement'); item.pop('interface')
                if config.get('remove_donor_comments',False):
                    projected=without_comments(item['code'])
                    if projected is not None and projected!=item['code']:
                        item['code']=projected
                        item['source_candidate_sha256']=item.pop('candidate_sha256')
                        item['displayed_code_sha256']=content_hash(projected)
                        item['code_projection']='ordinary_comments_removed; complete executable token sequence retained'
                # Reject impossible fits before deriving a full delta packet.
                if len(json.dumps(selected + [item], ensure_ascii=False)) > code_budget:
                    continue
                # Select with the full delta cost even in this ablation, so
                # removing its display cannot change which donors fit.
                item["contract_delta"] = contract_delta(task, donor)
                if len(json.dumps(selected + [item], ensure_ascii=False)) > code_budget:
                    continue
                selected.append(item)
                c = donor_clauses[donor["id"]]
                for side in covered:
                    covered[side].update(c[side] & query[side])
                accepted = True
                rejected.update(r['id'] for r in donors if r.get('task_id') == donor.get('task_id'))
                break
            donors = [r for r in donors if r["id"] not in rejected]
            if not accepted:
                break
    if not config.get('use_contract_delta',True):
        selected = [{k:v for k,v in item.items() if k != 'contract_delta'} for item in selected]
    if config.get('use_skill_memory',True):
        skills = [r for r in eligible if r['kind']=='verified_skill']
        for r in ranked(skills,task,int(config.get('skill_top_k',3))) if skills else []:
            remaining = maximum-len(json.dumps({'kind':'verified_contract_transfer_and_outcome_gated_repair','items':selected},ensure_ascii=False))-64
            item=compact_skill(r,min(int(config.get('skill_item_characters',1300)),remaining))
            if item:
                selected.append(item)
    failed_stages = failure_stages(feedback)
    if failed_stages and config.get("use_repair_memory", True) and int(config.get('repair_top_k',2)) > 0:
        repairs = [r for r in eligible if r["kind"] == "successful_trajectory_repair"
                   and (not config.get('use_failure_stage_filter',True) or failed_stages & set(r["trigger_stages"]))]
        audit['matching_repairs']=len(repairs)
        query = {**task,'requirement':task['requirement']+'\nObserved failure: '+failure_query(feedback)}
        repairs = ranked(repairs, query, int(config.get("repair_search_k", 16))) if repairs else []
        repair_count = 0
        repair_characters = 0
        for r in repairs:
            remaining = maximum-len(json.dumps({'kind':'verified_contract_transfer_and_outcome_gated_repair','items':selected},ensure_ascii=False))-64
            remaining = min(remaining,int(config.get('repair_memory_characters',6000))-repair_characters)
            if config.get('repair_representation','compact_diff') == 'full':
                item={k:r[k] for k in ['id','kind','trigger_stages','observed_failure','repair_hypothesis',
                       'before_code','after_code','after_feedback','trajectory_terminal_success','scope']}
                if len(json.dumps(item,ensure_ascii=False)) > remaining:
                    item=None
            else:
                item=compact_repair(r,min(int(config.get('repair_item_characters',2800)),remaining),feedback)
            if item is None:
                audit['repair_projections_rejected']+=1
                continue
            selected.append(item);repair_count+=1
            repair_characters+=len(json.dumps(item,ensure_ascii=False))+2
            if repair_count >= int(config.get('repair_top_k',2)):
                break
    result=packet(selected, maximum, label="verified_contract_transfer_and_outcome_gated_repair")
    audit.update(selected_ids=[r['id'] for r in result['items']],
                 selected_kinds=[r['kind'] for r in result['items']],
                 memory_characters=len(json.dumps(result,ensure_ascii=False)))
    return result
