#!/usr/bin/env python3
"""Prepare the frozen ST corpus, then build a method's reusable training assets."""
import argparse
import importlib
import json
from pathlib import Path

from baseline_common import RunContext
from baseline_common.binding import seal_adapter
from baseline_common.config import load_config
from baseline_common.datasets import load_prepared, prepare_training, write_json
from baseline_common.learning.provider import LearningProvider
from baseline_common.learning.runtime import make_embedder
from baseline_common.registry import LEARNED_ENCODERS, METHODS, MODEL_LEARNING
from baseline_common.utils import Redactor


def main(argv=None, *, default_method=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--dataset", type=Path, default=Path(__file__).parent / "final_train_datasets")
    prepare.add_argument("--output", type=Path, required=True)
    train = commands.add_parser("train")
    train.add_argument("--method", choices=METHODS, default=default_method, required=default_method is None)
    train.add_argument("--corpus", type=Path, required=True)
    train.add_argument("--config", type=Path, required=True)
    train.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    ctx = None
    try:
        if args.command == "prepare":
            result = prepare_training(args.dataset, args.output)
        else:
            if args.output.exists():
                raise ValueError("Training output already exists; use a new directory")
            corpus = load_prepared(args.corpus)
            config = load_config(args.config)
            settings = config["method"]
            embedder = make_embedder(settings["encoder"]) if args.method in LEARNED_ENCODERS else None
            provider = None
            if args.method in MODEL_LEARNING:
                task = {"id": "TRAINING_ASSET_BUILD", "requirement": "Learn only from the frozen training partition",
                        "target": "ST-training-corpus", "interface": {}, "files": {},
                        "entry_file": "unused.st", "editable_files": ["unused.st"], "public_properties": []}
                ctx = RunContext(task, config, Path(str(args.output) + ".audit"), method=args.method + "_training")
                provider = LearningProvider(ctx)
            module = importlib.import_module(METHODS[args.method] + ".training")
            summary = module.train(corpus, args.output, settings, provider=provider, embedder=embedder)
            result = seal_adapter(args.output, args.method, corpus, settings, summary)
            if ctx:
                ctx.write_artifact("training_result.json", {"status": "assets_built", "budget": ctx.budget.report(),
                                  "output": str(args.output.resolve()), "plc_evaluation_performed": False})
        print(json.dumps({"status": "complete", "output": str(args.output.resolve()),
                          "task_count": result.get("task_count", result.get("corpus_binding", {}).get("task_count"))}, ensure_ascii=False))
        return 0
    except Exception as exc:
        if ctx:
            ctx.write_artifact("training_failure.json", {"type": type(exc).__name__, "message": str(exc), "budget": ctx.budget.report()})
        print(json.dumps({"status": "failed", "type": type(exc).__name__, "message": Redactor().text(str(exc))}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
