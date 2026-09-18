#!/usr/bin/env python3
"""Run one ST task with a named baseline or the proposed method."""
import argparse
import importlib
import json
from pathlib import Path

from baseline_common import BudgetExceeded, ProtocolError, ProviderError, RunContext
from baseline_common.binding import verify_adapter
from baseline_common.config import load_config, load_task
from baseline_common.datasets import load_public_task
from baseline_common.registry import METHODS
from baseline_common.utils import Redactor


def main(argv=None, *, default_method=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=METHODS, default=default_method, required=default_method is None)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", type=Path, help="Explicit public task JSON")
    source.add_argument("--task-id", help="Task ID in the public test manifest")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).parent / "test_dataset")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    ctx = None
    try:
        task = load_task(args.task) if args.task else load_public_task(args.dataset, args.task_id)
        config = load_config(args.config)
        method_config = config["method"]
        if "memory_root" in method_config:
            method_config["memory_root"] = str((args.config.resolve().parent / method_config["memory_root"]).resolve())
            verify_adapter(method_config["memory_root"], args.method, task)
        elif args.method != "Vanilla":
            raise ProtocolError("Train this method and configure memory_root before generation")
        workflow = importlib.import_module(METHODS[args.method] + ".workflow")
        ctx = RunContext(task, config, args.output, method=args.method)
        result = workflow.run(task, ctx, method_config)
        if not ctx.finished:
            raise ProtocolError("Method returned without recording its result")
    except (BudgetExceeded, ProtocolError, ProviderError, OSError, ValueError, KeyError, ImportError) as exc:
        if ctx is None:
            print(json.dumps({"status": "configuration_error", "type": type(exc).__name__, "message": Redactor().text(str(exc))}, ensure_ascii=False))
            return 2
        ctx.record("run_exception", error_type=type(exc).__name__, message=str(exc))
        result = ctx.finish(ctx.latest_code, "incomplete", f"{type(exc).__name__}: {exc}",
                            files=ctx.latest_files if ctx.latest_files.get(task["entry_file"]) == ctx.latest_code else None,
                            details={"success_scope": "none", "learning_during_test": False})
    print(json.dumps({k: result[k] for k in ["method", "task_id", "status", "reason", "evidence_mode", "budget"]}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
