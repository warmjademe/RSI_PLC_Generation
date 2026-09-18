from baseline_common.memory import compatible, load, packet, ranked, resolve_root
from baseline_common.workflow import run_loop
import json


def project(record, maximum):
    result = {k: record[k] for k in ["id", "task_id", "target", "success", "terminal_status", "reward", "provenance"]}
    result.update(requirement=record["requirement"], interface=record["interface"], attempts=record["attempts"], projection="full")
    if len(json.dumps(result, ensure_ascii=False)) <= maximum - 256:
        return result
    result["requirement"] = record["requirement"][:3000]
    result["interface"] = record["interface"][:2500]
    result["projection"] = "bounded_raw_trace; long source fields explicitly truncated"
    result["attempts"] = [{"number": a["number"], "repair_mode": a["repair_mode"],
                           "hypothesis": a["hypothesis"][:500],
                           "feedback": [{"name": g.get("name"), "status": g.get("status"), "summary": str(g.get("summary", ""))[:200]} for g in a["feedback"]]}
                          for a in record["attempts"]]
    terminal = record["attempts"][-1]["candidate_st"] if record["attempts"] else ""
    result["terminal_candidate_st"] = terminal if len(terminal) <= 12000 else terminal[:8000] + "\n[TRUNCATED SOURCE]\n" + terminal[-3000:]
    while len(json.dumps(result, ensure_ascii=False)) > maximum - 256 and len(result["attempts"]) > 1:
        result["attempts"].pop(0)
        result["projection"] = "bounded_raw_trace; earliest attempts omitted; source fields may be truncated"
    return result


def run(task, ctx, config):
    _, bank = load(resolve_root(config), "RawTrajectoryRAG")
    def retrieve(task, feedback, ctx):
        matches = ranked(compatible(bank, task), task, int(config.get("top_k", 1)))
        # Never label a partial or failed trajectory as a successful program.
        maximum = int(config.get("memory_characters", 24000))
        return packet([project(r, maximum) for r in matches], maximum, label="recorded_training_episodes_with_actual_outcomes")
    return run_loop(task, ctx, config, retrieve)
