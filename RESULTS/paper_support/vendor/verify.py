"""Recompute RQ4 totals and check the published task and receipt audit records."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import statistics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recompute():
    audit = read(HERE / "vendor_audit.json")
    package = ROOT / "test_dataset_100_tasks"
    selection = read(package / "selection.json")["tasks"]
    selected = {row["task_id"]: row for row in selection}
    protocol = audit["protocol"]
    assert audit["status"] == "pass"
    assert len(selected) == protocol["tasks_per_model"] == 100
    assert protocol["candidate_limit"] == 10
    assert set(selected) == set(protocol["dataset_gate"]["task_ids"])
    assert sha256(package / "manifest.json") == protocol["dataset_gate"]["manifest_sha256"]
    for relative, digest in read(package / "manifest.json")["files"].items():
        assert sha256(package / relative) == digest, relative

    models = {}
    assert set(audit["models"]) == {"qwen", "deepseek", "haiku"}
    for model, entry in audit["models"].items():
        rows = entry["tasks"]
        assert len(rows) == 100 and {r["task_id"] for r in rows} == set(selected)
        assert sorted(r["order"] for r in rows) == list(range(1, 101))
        all_calls = []
        for row in rows:
            candidates = row["candidates"]
            assert 1 <= len(candidates) == row["candidate_count"] <= 10
            assert [c["id"] for c in candidates] == list(range(1, len(candidates) + 1))
            passing = [c["id"] for c in candidates if
                       c["stages"].get("compile") == c["stages"].get("runtime") == "pass"]
            assert row["first_success"] == min(passing, default=None)
            assert bool(passing) == (row["status"] == "passed")
            assert row["compile_passed"] == any(c["stages"].get("compile") == "pass" for c in candidates)
            assert sum(c["role"] == "plc.generate" for c in row["calls"]) == len(candidates)
            for call in row["calls"]:
                assert call["role"] in {"plc.generate", "knowledge.curate", "knowledge.review"}
                assert isinstance(call["missing"], bool)
                assert all(isinstance(call[k], int) and call[k] >= 0 for k in ("input", "output"))
            all_calls.extend(row["calls"])

        successes = {r["task_id"]: r for r in rows if r["first_success"] is not None}
        receipts = entry["passing_receipt_audits"]
        assert len(receipts) == len(successes)
        assert {r["task_id"] for r in receipts} == set(successes)
        for receipt in receipts:
            task_id = receipt["task_id"]
            assert receipt["candidate"] == successes[task_id]["first_success"]
            assert receipt["source_and_suite_binding"] == receipt["compile_and_runtime_same_candidate"] == "pass"
            assert re.fullmatch(r"[0-9a-f]{64}", receipt["code_hash"])
            expected = {c["id"] for c in read(package / selected[task_id]["tests_file"])["cases"]}
            cases = receipt["cases"]
            assert len(cases) == receipt["case_count"] == len(expected)
            assert {c["case_id"] for c in cases} == expected
            assert all(c["status"] == "pass" and c["target"] == "DVP48ES300R" for c in cases)
            images = {c["image_identity_sha256"] for c in cases}
            assert len(images) == 1 and re.fullmatch(r"[0-9a-f]{64}", next(iter(images)))

        states = Counter(r["status"] for r in rows)
        stages = Counter((c["stages"].get("compile", "not_run"), c["stages"].get("runtime", "not_run"))
                         for r in rows for c in r["candidates"])
        totals = dict(completed=len(rows), success=states["passed"], failed=states["failed"],
                      unknown=len(rows) - states["passed"] - states["failed"],
                      first_success=sum(r["first_success"] == 1 for r in rows),
                      compile_passed_tasks=sum(r["compile_passed"] for r in rows),
                      candidate_count=sum(r["candidate_count"] for r in rows),
                      mean_success_attempts=statistics.mean(r["first_success"] for r in successes.values()),
                      model_calls=len(all_calls), roles=dict(Counter(c["role"] for c in all_calls)),
                      input_tokens=sum(c["input"] for c in all_calls),
                      output_tokens=sum(c["output"] for c in all_calls),
                      missing_usage=sum(c["missing"] for c in all_calls),
                      candidate_stages={"/".join(k): v for k, v in stages.items()})
        totals["tokens"] = totals["input_tokens"] + totals["output_tokens"]
        assert totals == entry["totals"], model
        models[model] = totals

    for name, record in audit["source_manifest"].items():
        assert not Path(name).is_absolute() and ".." not in Path(name).parts
        assert record["bytes"] >= 0 and re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
    return dict(status="pass", target="DVP48ES300R", candidate_limit=10,
                tasks_per_model=100, vendor_audit_sha256=sha256(HERE / "vendor_audit.json"),
                dataset_manifest_sha256=sha256(package / "manifest.json"),
                cost_policy="Reported input/output tokens across generation and learning; missing usage counted separately.",
                source_manifest_entries=len(audit["source_manifest"]), models=models)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write the recomputed summary.json")
    args = parser.parse_args()
    summary = recompute()
    destination = HERE / "summary.json"
    if args.write:
        destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        assert read(destination) == summary, "summary.json differs from recomputed results"
    print("PASS: 300 task records, 123 passing receipt audits, candidate budgets and reported token totals.")


if __name__ == "__main__":
    main()
