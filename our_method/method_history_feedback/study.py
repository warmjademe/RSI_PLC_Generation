"""Freeze the public task stream; serial, crash-aware task boundary commits."""
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
from datetime import datetime, timezone

from baseline_common.config import load_task
from baseline_common.errors import ProtocolError
from baseline_common.providers import create_provider
from baseline_common.utils import Redactor, content_hash, object_hash, canonical_json
from . import VERSION
from .config import normalize
from .store import KnowledgeStore
from .workflow import solve

SOURCE = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = SOURCE / "test_datset_117_tasks"


def write_json(path, value):
    path = Path(path)
    if Redactor().clean(value) != value:
        raise ProtocolError("refusing to persist a recognizable credential")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(canonical_json(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def source_fingerprint():
    files = list(Path(__file__).parent.rglob("*.py")) + list((SOURCE / "baseline_common").glob("*.py"))
    files += [SOURCE / "our_method" / name for name in ("bound_edits.py", "validation_admission.py")]
    return object_hash({str(p.relative_to(SOURCE)): content_hash(p.read_text(encoding="utf-8")) for p in sorted(files)})


def load_public_tasks(dataset, expected_count=117):
    """Read ONLY public tasks/*.json; never manifest provenance or evaluator files."""
    root = Path(dataset).resolve() / "tasks"
    paths = sorted(root.glob("*.json"))
    if len(paths) != expected_count:
        raise ProtocolError(f"expected {expected_count} public task files, found {len(paths)}")
    tasks = []
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ProtocolError("public task symlinks are not accepted")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "project_root" in raw or "project_files" in raw:
            raise ProtocolError("online dataset accepts only inline public project files")
        task = load_task(path)
        if Redactor().clean(task) != task:
            raise ProtocolError("public task contains a recognizable credential")
        tasks.append(task)
    if len({t["id"] for t in tasks}) != len(tasks):
        raise ProtocolError("duplicate public task ID")
    return sorted(tasks, key=lambda t: t["id"])


def preflight(config, *, require_environment=True):
    config = normalize(config)
    errors = []
    provider = config.get("provider", {})
    kind = provider.get("kind")
    if kind not in ("replay", "openai_compatible", "anthropic"):
        errors.append("provider kind must be explicit")
    if kind != "replay":
        for key in ("model", "base_url", "api_key_env"):
            if not isinstance(provider.get(key), str) or not provider[key]:
                errors.append("provider needs " + key)
        if any("REPLACE_" in str(provider.get(key, "")) for key in ("model", "base_url")):
            errors.append("provider still contains an example placeholder")
        if require_environment and not os.environ.get(provider.get("api_key_env", "")):
            errors.append("configured provider environment variable is unset")
    for stage in config["validation_stages"]:
        validator = config.get("validators", {}).get(stage, {})
        if not isinstance(validator, dict):
            errors.append(stage + ": validator must be an object")
            continue
        command = validator.get("command")
        if (validator.get("kind") != "command" or not isinstance(command, list) or not command
                or not all(isinstance(a, str) and a for a in command)):
            errors.append(stage + ": configure an actual JSON validator command")
            continue
        if validator.get("protocol", "json") != "json":
            errors.append(stage + ": source-bound JSON receipts required")
        timeout = validator.get("timeout_seconds", 120)
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            errors.append(stage + ": timeout_seconds must be positive and finite")
        env = validator.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            errors.append(stage + ": validator environment must contain string names/values")
        exe = command[0].replace("{python}", sys.executable)
        if require_environment and not shutil.which(exe):
            errors.append(stage + ": executable unavailable")
        if any("REPLACE_" in a for a in command):
            errors.append(stage + ": unresolved command placeholder")
        for argument in command[1:]:
            if require_environment and argument.startswith("/") and argument.endswith(".py") and not Path(argument).is_file():
                errors.append(stage + ": validator script does not exist")
        if kind != "replay" and validator.get("test_only", False):
            errors.append(stage + ": synthetic test validator cannot be used for a live study")
    admission = config.get("validation_admission")
    if admission is not None and (not isinstance(admission, dict) or admission.get("slots") != 4
                                  or not isinstance(admission.get("directory"), str) or not admission["directory"]):
        errors.append("validation_admission requires four slots and a directory")
    return {"ready": not errors, "errors": errors, "calls_made": 0,
            "note": "Configuration check only; does not certify PLC tools or vendor hardware."}


def initialize(root, dataset, config, *, seed=20260914, expected_count=117, shuffle=True):
    root = Path(root).resolve()
    if root.exists():
        raise ProtocolError("study output must be new; use run/report to resume an existing study")
    config = normalize(config)
    tasks = load_public_tasks(dataset, expected_count)
    if shuffle:
        random.Random(seed).shuffle(tasks)
    manifest = {"schema_version": 1, "method_version": VERSION,
                "protocol": "online_task_boundary_knowledge_learning", "preloaded_history_records": 0,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "dataset": str(Path(dataset).resolve()), "seed": seed, "shuffled": shuffle,
                "task_ids": [t["id"] for t in tasks], "task_hashes": [object_hash(t) for t in tasks],
                "config_sha256": object_hash(config), "source_sha256": source_fingerprint(),
                "score_interpretation": "Within-stream adaptation; these tasks are not an independent held-out test."}
    root.mkdir(parents=True, mode=0o700)
    (root / "public_tasks").mkdir()
    for index, task in enumerate(tasks, 1):
        write_json(root / "public_tasks" / f"{index:04d}.json", task)
    write_json(root / "config.json", config)
    write_json(root / "study.json", manifest)
    store = KnowledgeStore(root / "knowledge.sqlite3", manifest)
    store.close()
    return manifest


@contextlib.contextmanager
def _study_lock(root):
    with (root / "run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProtocolError("another process is running this ordered study") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read(root):
    manifest = json.loads((root / "study.json").read_text(encoding="utf-8"))
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    if object_hash(config) != manifest["config_sha256"] or source_fingerprint() != manifest["source_sha256"]:
        raise ProtocolError("frozen configuration or implementation changed; do not silently mix protocols")
    return manifest, config


def _recover(directory, manifest, snapshot, order):
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    publication = json.loads((directory / "publication.json").read_text(encoding="utf-8"))
    task = json.loads((directory / "task.json").read_text(encoding="utf-8"))
    if (object_hash(task) != manifest["task_hashes"][order - 1]
            or result["task_id"] != manifest["task_ids"][order - 1]
            or result["details"]["publication_sha256"] != object_hash(publication)
            or publication["knowledge_before_sha256"] != snapshot["sha256"]
            or publication["order"] != order):
        raise ProtocolError("finished-task recovery provenance mismatch")
    return {**publication, "result_hash": object_hash(result), "result": result}


def run(root, *, max_tasks=None, provider_factory=None):
    root = Path(root).resolve()
    if max_tasks is not None and (type(max_tasks) is not int or max_tasks < 1):
        raise ProtocolError("max_tasks must be positive")
    with _study_lock(root):
        manifest, config = _read(root)
        ready = preflight(config)
        if not ready["ready"]:
            raise ProtocolError("preflight failed: " + "; ".join(ready["errors"]))
        store = KnowledgeStore(root / "knowledge.sqlite3", manifest)
        try:
            completed = store.completed()
            # Both committed outputs and public inputs remain part of the audit chain.
            for old in completed:
                saved = json.loads((root / "runs" / f'{old["order"]:04d}' / "result.json").read_text())
                if object_hash(saved) != old["result_hash"]:
                    raise ProtocolError("a committed task result changed")
            remaining = len(manifest["task_ids"]) - len(completed)
            count = min(remaining, max_tasks if max_tasks is not None else remaining)
            for order in range(len(completed) + 1, len(completed) + count + 1):
                task = json.loads((root / "public_tasks" / f"{order:04d}.json").read_text(encoding="utf-8"))
                if object_hash(task) != manifest["task_hashes"][order - 1]:
                    raise ProtocolError("frozen public task changed")
                snapshot = store.snapshot(order)
                directory = root / "runs" / f"{order:04d}"
                if directory.exists():
                    if not (directory / "result.json").is_file() or not (directory / "publication.json").is_file():
                        raise ProtocolError(f"interrupted task {order}: preserve its charged calls; automatic budget reset is forbidden")
                else:
                    provider = provider_factory(task, order) if provider_factory else create_provider(config["provider"])
                    solve(task, config, directory, order=order, snapshot=snapshot,
                          prior_evidence=lambda ids: store.evidence(ids, order), provider=provider)
                bundle = _recover(directory, manifest, snapshot, order)
                store.commit(bundle)
                print(canonical_json({"task_order": order, "task_id": task["id"],
                                      "status": bundle["result"]["status"], "knowledge_updates": len(bundle["updates"])}), flush=True)
        finally:
            store.close()
        return report(root)


def report(root):
    root = Path(root).resolve()
    store = KnowledgeStore(root / "knowledge.sqlite3")
    try:
        completed = store.completed()
        snapshot = store.snapshot(len(completed) + 1)
        rows, totals, role_calls = [], {"model_calls": 0, "tool_calls": 0, "total_tokens": 0}, {}
        for bundle in completed:
            result = bundle["result"]
            row = {"order": bundle["order"], "task_id": bundle["task_id"], "status": result["status"],
                   "first_candidate_passed": result["details"]["first_candidate_passed"],
                   "candidates": result["budget"]["candidates"], "evidence_mode": result["evidence_mode"],
                   "knowledge_offered_ids": result["details"]["knowledge_offered_ids"],
                   "model_reported_used_ids": result["details"]["model_reported_used_ids"],
                   "learning_status": result["details"]["learning_status"]}
            rows.append(row)
            for key in totals:
                totals[key] += result["budget"][key]
            for path in (root / "runs" / f'{bundle["order"]:04d}' / "model").glob("*/request.json"):
                role = json.loads(path.read_text(encoding="utf-8"))["role"]
                role_calls[role] = role_calls.get(role, 0) + 1
        out = {"protocol": "online_task_boundary_knowledge_learning", "preloaded_history_records": 0,
               "completed": len(rows), "total_tasks": len(store.manifest["task_ids"]),
               "passed": sum(r["status"] == "passed" for r in rows),
               "first_candidate_passed": sum(r["first_candidate_passed"] for r in rows),
               "active_claims": sum(r["status"] == "active" for r in snapshot["records"]),
               "draft_or_withdrawn_claims": sum(r["status"] != "active" for r in snapshot["records"]),
               "budget_totals": totals, "model_calls_by_role": role_calls, "tasks": rows,
               "benchmark_score": None, "interpretation": store.manifest["score_interpretation"]}
        write_json(root / "report.json", out)
        write_json(root / "knowledge_latest.json", snapshot)
        return out
    finally:
        store.close()
