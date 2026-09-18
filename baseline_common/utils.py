from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ProtocolError


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def object_hash(value: Any) -> str:
    return content_hash(canonical_json(value))


def safe_relative(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ProtocolError("file paths must be nonempty relative POSIX paths")
    p = PurePosixPath(name)
    if p.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise ProtocolError("absolute paths and parent traversal are forbidden")
    if ":" in name or any(part == ".git" or part.startswith(".env") for part in p.parts):
        raise ProtocolError("project path contains a protected component")
    return p.as_posix()


def validate_files(files: Any) -> dict[str, str]:
    if not isinstance(files, dict):
        raise ProtocolError("files must map relative paths to UTF-8 text")
    out = {}
    for name, value in files.items():
        if not isinstance(value, str):
            raise ProtocolError("project file contents must be strings")
        out[safe_relative(name)] = value
    return out


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
        if not match:
            raise ProtocolError("model response must contain one complete JSON object")
        text = match.group(1)
    try:
        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, TypeError) as exc:
        raise ProtocolError("model/tool response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("model/tool response must be a JSON object")
    return value


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (OSError, ValueError) as exc:
        raise ProtocolError(f"cannot read JSON file: {path.name}") from exc


def apply_patches(code: str, patches: list[dict]) -> str:
    """Require an unambiguous source match; never silently apply a no-op patch."""
    if not isinstance(patches, list) or not patches:
        raise ProtocolError("patches must be a nonempty list")
    updated = code
    for patch in patches:
        if not isinstance(patch, dict) or not isinstance(patch.get("old"), str) or not isinstance(patch.get("new"), str):
            raise ProtocolError("each patch needs string old and new fields")
        old, new = patch["old"], patch["new"]
        if not old or old == new or updated.count(old) != 1:
            raise ProtocolError("patch old text must match exactly once and change the source")
        updated = updated.replace(old, new, 1)
    return updated


class Redactor:
    """Keep configured credentials and recognizable tokens out of artifacts."""

    def __init__(self, secrets: list[str] = ()):
        self.secrets = sorted({s for s in secrets if s}, key=len, reverse=True)
        self.pattern = re.compile(
            r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
            r"github_pat_[A-Za-z0-9_]{20,}|hf_[A-Za-z0-9]{20,}|olp_[A-Za-z0-9]{15,})"
        )

    def text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        return self.pattern.sub("[REDACTED]", value)

    def clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {str(k): self.clean(v) for k, v in value.items()}
        if isinstance(value, tuple):
            return tuple(self.clean(v) for v in value)
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        return value
