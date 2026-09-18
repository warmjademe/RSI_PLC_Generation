from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .protocol import canonical, digest


class ArtifactError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(canonical(value).decode("utf-8"))
            handle.write("\n")
    temporary.replace(path)


def read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    result = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ArtifactError(
                    f"invalid JSONL at {path}:{line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise ArtifactError(
                    f"JSONL item is not an object at {path}:{line_number}"
                )
            result.append(value)
    return tuple(result)


def inventory(root: Path, *, excluding: set[str] | None = None) -> dict[str, str]:
    excluded = excluding or set()
    return {
        str(path.relative_to(root)): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and str(path.relative_to(root)) not in excluded
    }


def seal_manifest(root: Path, core: dict[str, Any]) -> dict[str, Any]:
    manifest_path = root / "freeze_manifest.json"
    if manifest_path.exists():
        raise ArtifactError("freeze manifest already exists")
    artifacts = inventory(root)
    document_core = {**core, "artifact_sha256": artifacts}
    document = {**document_core, "manifest_sha256": digest(document_core)}
    write_json(manifest_path, document)
    return document


def verify_manifest(
    root: Path,
    *,
    method: str,
    protocol_sha256: str,
) -> dict[str, Any]:
    path = root / "freeze_manifest.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError("freeze manifest is unreadable") from exc
    if not isinstance(document, dict):
        raise ArtifactError("freeze manifest must be an object")
    core = {
        key: value for key, value in document.items() if key != "manifest_sha256"
    }
    if document.get("manifest_sha256") != digest(core):
        raise ArtifactError("freeze manifest self-hash mismatch")
    if document.get("method") != method:
        raise ArtifactError("freeze method mismatch")
    if document.get("protocol_sha256") != protocol_sha256:
        raise ArtifactError("freeze uses a different evaluation protocol")
    # The adapter adds a separately checked corpus/query-isolation manifest after
    # the migrated engine freezes its own artifacts.
    actual = inventory(root, excluding={"freeze_manifest.json", "adapter_manifest.json", "adapter_inputs.json"})
    if document.get("artifact_sha256") != actual:
        raise ArtifactError("frozen method artifacts changed")
    return document
