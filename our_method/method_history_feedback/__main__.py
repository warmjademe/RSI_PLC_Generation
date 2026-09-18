import argparse
import json
from pathlib import Path
import sys

from baseline_common.errors import ProtocolError
from .config import load_config
from .study import DEFAULT_DATASET, initialize, load_public_tasks, preflight, report, run


def main():
    parser = argparse.ArgumentParser(description="Online PLC task feedback and task-boundary knowledge learning")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("init", "preflight"):
        p = sub.add_parser(action)
        p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
        p.add_argument("--config", type=Path, required=True)
        if action == "init":
            p.add_argument("--output", type=Path, required=True)
            p.add_argument("--seed", type=int, default=20260914)
            p.add_argument("--no-shuffle", action="store_true")
    for action in ("run", "report"):
        p = sub.add_parser(action)
        p.add_argument("--study", type=Path, required=True)
        if action == "run":
            p.add_argument("--max-tasks", type=int)
    p = sub.add_parser("demo")
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "init":
            result = initialize(args.output, args.dataset, load_config(args.config),
                                seed=args.seed, shuffle=not args.no_shuffle)
        elif args.action == "preflight":
            tasks = load_public_tasks(args.dataset)
            result = {**preflight(load_config(args.config)), "public_task_count": len(tasks)}
        elif args.action == "run":
            result = run(args.study, max_tasks=args.max_tasks)
        elif args.action == "report":
            result = report(args.study)
        else:
            from .demo import demo
            result = demo(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("ready") is False else 0
    except (ProtocolError, OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
