"""Explicit train-only import and public-only ST task loading."""
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
import gzip
import hashlib
import json

from .errors import ProtocolError
from .learning.models import AttemptStep, TaskQuery, TrajectoryEpisode
from .learning.protocol import canonical, digest


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rows(path):
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def write_rows(path, values):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for value in values:
            f.write(canonical(value).decode() + "\n")


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def public_metadata(meta):
    # Keep only source public contract and retrieval fields, never oracle/review internals.
    keys = ("id", "split", "category_id", "category", "semantic_signature", "family_id",
            "contamination_group_id", "source_categories", "interface", "requirements",
            "assumptions", "scan", "iec_features", "retrieval")
    return {k: meta[k] for k in keys if k in meta}


def retrieval_text(meta):
    return json.dumps({k: meta.get(k) for k in ("category", "iec_features", "retrieval")}, ensure_ascii=False)


def episode(public, attempts, *, key, success, status, provenance):
    meta = public_metadata(public["metadata"])
    return TrajectoryEpisode(
        task_id=public["task_id"], run_key=key, target=public["target"], output_language="st",
        category_id=meta.get("category_id", ""), category=meta.get("category", ""),
        semantic_signature=meta.get("semantic_signature", ""), requirement=public["requirement"],
        interface=public["interface"], public_metadata=meta, retrieval_text=retrieval_text(meta),
        attempts=tuple(attempts), success=success, terminal_status=status,
        reward=1.0 if success else 0.0, provenance=provenance)


def _ledger(text):
    previous = "0" * 64
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        event = json.loads(line)
        core = {k: v for k, v in event.items() if k != "event_hash"}
        if event["sequence"] != number or event["previous_hash"] != previous or digest(core) != event["event_hash"]:
            raise ProtocolError("Historical evidence ledger hash chain is invalid")
        previous = event["event_hash"]
        result.append(event)
    return result


def prepare_training(package, output):
    package, output = Path(package).resolve(), Path(output).resolve()
    manifest = json.loads((package / "manifest.json").read_text())
    if manifest.get("status") != "complete" or manifest.get("task_count") != 1000:
        raise ProtocolError("Expected the completed 1000-task training delivery")
    inputs = ["data/public_tasks.jsonl.gz", "data/success_cases.jsonl.gz",
              "process/trajectories.jsonl.gz", "process/records.jsonl.gz"]
    for name in inputs:
        if sha(package / name) != manifest["files"][name]["sha256"]:
            raise ProtocolError(f"Training delivery digest mismatch: {name}")
    public = {r["task_id"]: r for r in rows(package / inputs[0])}
    if len(public) != 1000 or any(r["metadata"].get("split") != "train" or not tid.startswith("TR_") for tid, r in public.items()):
        raise ProtocolError("Only the original 1000 TR training tasks may enter learning")
    examples = {}
    for row in rows(package / inputs[1]):
        if row["output_language"] != "st":
            continue
        if row["task_id"] not in public or row["status"] != "portable_verified_success" or row["test_split_accessed"] is not False:
            raise ProtocolError("Unverified or non-training success record")
        if digest({k: v for k, v in row.items() if k != "record_sha256"}) != row["record_sha256"]:
            raise ProtocolError("Success record digest mismatch")
        if hashlib.sha256(row["program_st"].encode()).hexdigest() != row["candidate_sha256"]:
            raise ProtocolError("Canonical ST digest mismatch")
        task = public[row["task_id"]]
        if any(row[k] != task[k] for k in ["target", "requirement", "interface"]):
            raise ProtocolError("Successful code is bound to another public contract")
        if row["task_id"] in examples:
            raise ProtocolError("Duplicate canonical ST implementation")
        examples[row["task_id"]] = {
            "id": row["task_id"], "task_id": row["task_id"], "target": row["target"],
            "output_language": "st", "requirement": row["requirement"], "interface": row["interface"],
            "code": row["program_st"], "candidate_sha256": row["candidate_sha256"],
            "record_sha256": row["record_sha256"], "metadata": public_metadata(task["metadata"]),
            "origin": row.get("candidate_origin", "model_generated"),
            "verification_scope": row["verification_scope"],
        }
    if set(examples) != set(public):
        raise ProtocolError("Missing canonical ST implementations")

    trajectories = {}
    skipped = Counter()
    for row in rows(package / inputs[2]):
        m = row["manifest"]
        if m.get("output_language") != "st":
            continue
        if not row["matches_frozen_public_snapshot"]:
            skipped["old_contract_trajectory"] += 1
            continue
        if m["task_id"] not in public or m["target"] != public[m["task_id"]]["target"]:
            raise ProtocolError("Trajectory is outside the training contract")
        prefix = str(Path(row["path"]).parent / "harness")
        trajectories[prefix] = row
    # Stream the archive once; retain only hash-bound candidates and ledger feedback.
    ledgers, codes = {}, {}
    for row in rows(package / inputs[3]):
        name = row["path"]
        prefix, sep, rest = name.partition("/harness/")
        prefix += "/harness"
        if not sep or prefix not in trajectories:
            continue
        is_ledger = rest == "ledger.jsonl"
        is_candidate = rest.startswith("attempts/attempt_") and rest.endswith("/candidate.st")
        if not (is_ledger or is_candidate):
            continue
        data = row["text"].encode()
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ProtocolError("Historical text integrity mismatch")
        if is_ledger:
            ledgers[prefix] = _ledger(row["text"])
        else:
            codes[name] = row["text"]
    histories, by_task = [], defaultdict(list)
    for prefix, raw in sorted(trajectories.items()):
        m = raw["manifest"]
        attempts = []
        if prefix not in ledgers:
            raise ProtocolError("Current-contract trajectory has no archived ledger")
        for event in ledgers[prefix]:
            if event["event_type"] != "candidate_evaluated":
                continue
            p = event["payload"]
            number = int(p["number"])
            name = f"{prefix}/attempts/attempt_{number:02d}/candidate.st"
            code = codes.get(name, "")
            if code and hashlib.sha256(code.encode()).hexdigest() != p.get("candidate_sha256"):
                raise ProtocolError("Historical candidate is not bound to its ledger")
            feedback = tuple({k: g[k] for k in ["name", "status", "summary", "evidence"] if k in g}
                             for g in p.get("gates", []))
            attempts.append(AttemptStep(number, p.get("repair_mode", ""), p.get("candidate", {}).get("hypothesis", ""),
                                        code, None, "", feedback, p.get("usage", {})))
        if not attempts:
            skipped["no_evaluated_candidate"] += 1
            continue
        ep = episode(public[m["task_id"]], attempts, key=m["collection_id"] + "/" + m["run_key"],
                     success=m.get("success") is True, status=m["terminal_status"],
                     provenance={"trajectory_path": raw["path"], "manifest_sha256": raw["sha256"],
                                 "completed_at": m.get("completed_at", ""), "kind": "recorded_trajectory"})
        histories.append(ep)
        by_task[ep.task_id].append(ep)
    selected, final_only = [], 0
    for tid in sorted(public):
        # A task contributes exactly one learning episode to each named baseline.
        # The latest recorded current-contract outcome is retained, including failure.
        if by_task[tid]:
            selected.append(max(by_task[tid], key=lambda e: (e.provenance["completed_at"], e.run_key)))
        else:
            final_only += 1
            r = examples[tid]
            step = AttemptStep(1, "verified_final_artifact_only", "", r["code"], None, "",
                               ({"name": "canonical_admission", "status": "pass", "summary": "portable_training_validation; no repair history inferred"},), {})
            selected.append(episode(public[tid], [step], key=tid + "/canonical-final",
                                    success=True, status="portable_verified_success",
                                    provenance={"kind": "final_artifact_only", "record_sha256": r["record_sha256"]}))
    output.mkdir(parents=True, exist_ok=False)
    write_rows(output / "examples.jsonl.gz", [examples[t] for t in sorted(examples)])
    write_rows(output / "episodes.jsonl.gz", [asdict(e) for e in selected])
    write_rows(output / "historical_episodes.jsonl.gz", [asdict(e) for e in histories])
    info = {
        "schema_version": 1, "split": "train", "languages": ["st"], "task_count": 1000,
        "successful_st_programs": 1000, "learning_episode_count": len(selected),
        "recorded_history_count": len(histories), "final_artifact_only_count": final_only,
        "selected_episode_outcomes": dict(Counter(e.success for e in selected)),
        "skipped": dict(skipped), "test_split_accessed": False,
        "episode_selection": "latest current-contract recorded episode, otherwise explicitly marked canonical final-only artifact",
        "source_package_manifest_sha256": sha(package / "manifest.json"),
        "source_files": {n: manifest["files"][n]["sha256"] for n in inputs},
        "files": {p.name: sha(p) for p in sorted(output.glob("*.gz"))},
    }
    write_json(output / "manifest.json", info)
    return info


def load_prepared(root):
    root = Path(root).resolve()
    info = json.loads((root / "manifest.json").read_text())
    if info.get("split") != "train" or info.get("languages") != ["st"] or info.get("test_split_accessed") is not False:
        raise ProtocolError("Learning input is not a frozen ST training corpus")
    for name, value in info["files"].items():
        p = root / name
        if p.is_symlink() or not p.resolve().is_relative_to(root) or sha(p) != value:
            raise ProtocolError("Prepared training corpus has changed")
    examples = list(rows(root / "examples.jsonl.gz"))
    def materialize(name):
        result = []
        for r in rows(root / name):
            r["attempts"] = tuple(AttemptStep(**a) for a in r["attempts"])
            result.append(TrajectoryEpisode(**r))
        return result
    return info, examples, materialize("episodes.jsonl.gz"), materialize("historical_episodes.jsonl.gz")


def load_public_task(root, task_id):
    root = Path(root).resolve()
    conf = json.loads((root / "dataset_config.json").read_text())
    if conf["languages"] != ["st"]:
        raise ProtocolError("Only the ST test subset is supported")
    found = [r for r in rows(root / "manifest.jsonl") if r["id"] == task_id]
    if len(found) != 1:
        raise ProtocolError("Task is not uniquely listed in the public manifest")
    row = found[0]
    folder = root / "public/tasks" / task_id
    if not folder.resolve().is_relative_to(root / "public/tasks") or folder.is_symlink():
        raise ProtocolError("Public task path escapes its boundary")
    for name in ["requirement.md", "interface.st", "metadata.json"]:
        if (folder / name).is_symlink() or sha(folder / name) != row["hashes"][name]:
            raise ProtocolError("Public task source hash mismatch")
    meta = json.loads((folder / "metadata.json").read_text())
    return {"id": task_id, "requirement": (folder / "requirement.md").read_text(),
            "target": meta["target"]["model"], "interface": meta["interface"],
            "interface_st": (folder / "interface.st").read_text(), "language": "st",
            "metadata": public_metadata(meta), "files": {}, "entry_file": "candidate.st",
            "editable_files": ["candidate.st"], "public_properties": []}


def task_query(task):
    meta = task.get("metadata", {})
    return TaskQuery(task["id"], task["target"], "st", meta.get("category_id", ""),
                     meta.get("category", ""), meta.get("semantic_signature", ""), task["requirement"],
                     task.get("interface_st", json.dumps(task["interface"], ensure_ascii=False)),
                     meta, retrieval_text(meta))
