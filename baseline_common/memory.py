"""Frozen artifact storage, filtering and context budgeting shared across methods."""
from pathlib import Path
import json

from .datasets import rows, sha, write_json, write_rows
from .errors import ProtocolError
from .learning.protocol import digest
from .retrieval import rank_records


def freeze(root, *, method, records, corpus, settings=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    write_rows(root / "records.jsonl.gz", records)
    manifest = {"schema_version": 1, "method": method, "split": "train", "languages": ["st"],
                "learning_during_test": False, "base_model_weights_updated": False,
                "corpus_binding": corpus, "settings": settings or {},
                "files": {"records.jsonl.gz": sha(root / "records.jsonl.gz")}}
    manifest["manifest_sha256"] = digest(manifest)
    write_json(root / "manifest.json", manifest)
    return manifest


def load(root, method):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if digest({k: v for k, v in manifest.items() if k != "manifest_sha256"}) != manifest["manifest_sha256"]:
        raise ProtocolError("Memory manifest changed")
    if manifest["method"] != method or manifest["split"] != "train" or manifest["languages"] != ["st"]:
        raise ProtocolError("Frozen memory method/split/language mismatch")
    for name, value in manifest["files"].items():
        p = root / name
        if p.is_symlink() or not p.resolve().is_relative_to(root) or sha(p) != value:
            raise ProtocolError("Frozen memory changed")
    return manifest, list(rows(root / "records.jsonl.gz"))


def compatible(records, task):
    result = []
    query_meta = task.get("metadata", {})
    for r in records:
        if r.get("target") != task["target"] or r.get("output_language", "st") != "st":
            continue
        if task["id"] in {r.get("id"), r.get("task_id"), r.get("source_task_id")}:
            continue
        meta = r.get("metadata", {})
        if any(query_meta.get(k) and query_meta[k] == meta.get(k) for k in ["contamination_group_id", "semantic_signature"]):
            continue
        result.append(r)
    return result


def ranked(records, task, k):
    # Exclude source code from similarity: only public requirement/interface drive retrieval.
    projected = [{"id": str(i), "requirement": r.get("requirement", "") + "\n" + str(r.get("interface", ""))}
                 for i, r in enumerate(records)]
    matches = rank_records(task["requirement"] + "\n" + str(task.get("interface_st", task["interface"])), projected, k)
    return [records[int(r["id"])] for r in matches]


def packet(records, maximum, *, label):
    selected = []
    for record in records:
        proposed = {"kind": label, "items": selected + [record]}
        if len(json.dumps(proposed, ensure_ascii=False)) <= maximum:
            selected.append(record)
    return {"kind": label, "items": selected}


def code_view(record):
    return {k: record[k] for k in ["id", "task_id", "target", "requirement", "interface", "code", "candidate_sha256"] if k in record}


def resolve_root(config):
    value = config.get("memory_root")
    if not value:
        raise ProtocolError("This method requires an explicitly trained memory_root")
    return Path(value).resolve()
