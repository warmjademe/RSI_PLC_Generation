from pathlib import Path
import json
from .datasets import sha, write_json
from .errors import ProtocolError
from .learning.protocol import digest


def seal_adapter(root, method, corpus, config, summary):
    root = Path(root)
    identities = [{"id": r["id"], **{k: r["metadata"].get(k) for k in ["contamination_group_id", "semantic_signature"]}}
                  for r in corpus[1]]
    write_json(root / "adapter_inputs.json", identities)
    doc = {"schema_version": 1, "status": "complete", "method": method,
           "corpus_binding": corpus[0], "training_config": config, "training_summary": summary,
           "learning_during_test": False, "test_split_accessed": False,
           "files": {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}}
    doc["manifest_sha256"] = digest(doc)
    write_json(root / "adapter_manifest.json", doc)
    return doc


def verify_adapter(root, method, task=None):
    root = Path(root).resolve()
    doc = json.loads((root / "adapter_manifest.json").read_text())
    if doc.get("status") != "complete" or doc.get("method") != method or doc.get("test_split_accessed") is not False:
        raise ProtocolError("Learning output is not a completed training-only asset")
    if digest({k: v for k, v in doc.items() if k != "manifest_sha256"}) != doc["manifest_sha256"]:
        raise ProtocolError("Asset binding manifest changed")
    for name, expected in doc["files"].items():
        p = root / name
        if p.is_symlink() or not p.resolve().is_relative_to(root) or sha(p) != expected:
            raise ProtocolError("Frozen learned asset changed")
    if task is not None:
        meta = task.get("metadata", {})
        for item in json.loads((root / "adapter_inputs.json").read_text()):
            if item["id"] == task["id"] or any(meta.get(k) and meta[k] == item.get(k)
                 for k in ["contamination_group_id", "semantic_signature"]):
                raise ProtocolError("Evaluation query overlaps the learned training identities")
    return doc
