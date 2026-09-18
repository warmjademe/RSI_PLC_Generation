from baseline_common.memory import freeze


def records_from_corpus(corpus):
    records = [{**r, "kind": "verified_program"} for r in corpus[1]]
    meta = {r["task_id"]: r["metadata"] for r in corpus[1]}
    for ep in corpus[3]:
        if not ep.success or ep.provenance.get("kind") != "recorded_trajectory":
            continue
        for previous, following in zip(ep.attempts, ep.attempts[1:]):
            failures = [g for g in previous.feedback if g.get("status") == "fail"]
            if not failures or not previous.candidate_st or not following.candidate_st:
                continue
            if previous.candidate_st == following.candidate_st:
                continue
            records.append({
                "id": f"repair:{ep.run_key}:{previous.number}:{following.number}",
                "task_id": ep.task_id, "source_task_id": ep.task_id,
                "kind": "successful_trajectory_repair", "target": ep.target, "output_language": "st",
                "requirement": ep.requirement, "interface": ep.interface, "metadata": meta[ep.task_id],
                "trigger_stages": [g["name"] for g in failures], "observed_failure": failures,
                "repair_hypothesis": following.hypothesis, "before_code": previous.candidate_st,
                "after_code": following.candidate_st, "after_feedback": following.feedback,
                "trajectory_terminal_success": True, "provenance": ep.provenance,
                "scope": "observed transition in an ultimately successful training episode; not causal attribution",
            })
    return records


def train(corpus, output, config, **kwargs):
    records = records_from_corpus(corpus)
    return freeze(output, method="OurMethod", records=records, corpus=corpus[0], settings={
        "learning_model_calls": 0, "contract_transfer": True, "outcome_gated_repairs": True,
        "program_count": len(corpus[1]), "repair_count": len(records) - len(corpus[1]),
    })
