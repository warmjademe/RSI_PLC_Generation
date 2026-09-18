#!/usr/bin/env python3
"""Verify the delivery offline; optionally exercise the real prepared corpus."""
import argparse
import ast
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from baseline_common.binding import seal_adapter, verify_adapter
from baseline_common.datasets import load_prepared, load_public_task, sha, write_json
from baseline_common.registry import METHODS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, help="Already verified training preparation output; never calls models")
    parser.add_argument("--report", type=Path, default=ROOT / "DELIVERY_VALIDATION.json")
    args = parser.parse_args()
    report = {"status": "running", "methods": list(METHODS), "paid_model_calls": 0,
              "real_plc_validation": "not_run", "ppo_numeric_training": "not_run_missing_optional_torch"}
    roots = [ROOT / name for name in METHODS.values()] + [ROOT / "baseline_common", ROOT / "tests", ROOT / "tools"]
    sources = [p for root in roots for p in root.rglob("*.py")] + [ROOT / "run_baseline.py", ROOT / "train_baseline.py"]
    for path in sources:
        ast.parse(path.read_text(), filename=str(path))
    report["python_source_count"] = len(sources)
    report["source_sha256"] = {str(p.relative_to(ROOT)): sha(p) for p in sorted(sources)}
    for name, folder in METHODS.items():
        for file in ["README.md", "workflow.py", "training.py", "example_config.json", "training_config.json", "__main__.py", "tests/test_method.py"]:
            if not (ROOT / folder / file).is_file():
                raise RuntimeError(f"Missing method delivery file: {folder}/{file}")
        result = subprocess.run([sys.executable, "-B", "-m", folder, "--help"], cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"Method CLI help failed: {name}")
    report["method_cli_help_count"] = len(METHODS)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT / "tests")))
    report["unit_tests"] = {"run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}
    (args.report.parent / "DELIVERY_TESTS.log").write_text(stream.getvalue())
    if not result.wasSuccessful():
        report["status"] = "fail"
        write_json(args.report, report)
        print(stream.getvalue())
        return 1
    with tempfile.TemporaryDirectory(prefix="plc-comparison-", dir="/dev/shm") as temporary:
        workspace = Path(temporary)
        # Exercise the actual command path without a configured model or PLC tool.
        run = subprocess.run([sys.executable, "-B", str(ROOT / "run_baseline.py"), "--method", "Vanilla",
                              "--task-id", "TE_C01_C01_01", "--config", str(ROOT / "baseline_Vanilla/example_config.json"),
                              "--output", str(workspace / "vanilla-demo")], cwd=ROOT, capture_output=True, text=True)
        demo = json.loads((workspace / "vanilla-demo/result.json").read_text())
        if run.returncode != 1 or demo["status"] != "incomplete" or demo["budget"]["model_calls"] != 1:
            raise RuntimeError("Unconfigured CLI demo must stop incomplete after one replay generation")
        report["actual_cli_demo"] = {"status": demo["status"], "model_calls": 1, "evidence_mode": demo["evidence_mode"]}
        if args.prepared:
            corpus = load_prepared(args.prepared)
            report["real_training_input"] = corpus[0]
            selected = json.loads((ROOT / "test_dataset/selection.json").read_text())["selected_task_ids"]
            tasks = [load_public_task(ROOT / "test_dataset", tid) for tid in selected]
            report["public_st_queries_loaded"] = len(tasks)
            fit_results = {}
            for method in ["Vanilla", "FewShot", "FinalCodeRAG", "RawTrajectoryRAG", "OurMethod"]:
                output = workspace / method
                module = importlib.import_module(METHODS[method] + ".training")
                summary = module.train(corpus, output, {})
                seal_adapter(output, method, corpus, {}, summary)
                verify_adapter(output, method, tasks[0])
                identities = json.loads((output / "adapter_inputs.json").read_text())
                for task in tasks:
                    if any(task["id"] == item["id"] or any(task["metadata"].get(k) == item.get(k)
                           for k in ["contamination_group_id", "semantic_signature"]) for item in identities):
                        raise RuntimeError("A selected test query overlaps training identities")
                fit_results[method] = {"asset_build": "pass", "source_task_count": len(identities), "test_identity_overlap": 0}
                if method == "OurMethod":
                    from baseline_common.memory import load
                    from our_method.retrieval import retrieve
                    _, bank = load(output, method)
                    contexts = [retrieve(bank, task, [], {}) for task in tasks]
                    fit_results[method].update(repair_records=summary["settings"]["repair_count"],
                                              public_queries_with_program_context=sum(bool(c["items"]) for c in contexts))
            report["real_corpus_asset_builds"] = fit_results
    report["status"] = "pass"
    write_json(args.report, report)
    print(json.dumps({"status": report["status"], "methods": len(METHODS), "tests": report["unit_tests"],
                      "real_corpus_checked": bool(args.prepared), "report": str(args.report)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
