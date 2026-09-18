from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from .errors import ProtocolError
from .utils import read_json, safe_relative, validate_files


def load_task(path: str | Path) -> dict:
    path = Path(path).resolve()
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ProtocolError("task must be an object")
    for key in ("id", "requirement", "target"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ProtocolError(f"task needs nonempty {key}")
    if raw.get("language", "ST").upper() != "ST":
        raise ProtocolError("the comparison methods support ST only")
    files = validate_files(raw.get("files", {}))
    # Project input is an explicit manifest, never an unrestricted directory walk.
    if "project_root" in raw:
        root = (path.parent / raw["project_root"]).resolve()
        selected = raw.get("project_files")
        if not isinstance(selected, list):
            raise ProtocolError("project_root requires an explicit project_files allowlist")
        for name in selected:
            name = safe_relative(name)
            candidate = root / name
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
                raise ProtocolError("project symlinks must not escape the selected project")
            if name in files:
                raise ProtocolError("duplicate inline and disk project file")
            try:
                files[name] = candidate.read_text(encoding="utf-8")
            except OSError as exc:
                raise ProtocolError("cannot read selected project file") from exc
    entry = safe_relative(raw.get("entry_file", "candidate.st"))
    editable = raw.get("editable_files", [entry])
    if not isinstance(editable, list) or not editable:
        raise ProtocolError("editable_files must be a nonempty list")
    editable = [safe_relative(item) for item in editable]
    if entry not in editable:
        raise ProtocolError("entry_file must be editable")
    interface = raw.get("interface", {})
    properties = raw.get("public_properties", [])
    if not isinstance(interface, dict) or not isinstance(properties, list):
        raise ProtocolError("interface must be an object and public_properties a list")
    # Deliberately do not forward raw metadata, reference code or evaluator fields.
    return {"id": raw["id"], "requirement": raw["requirement"], "target": raw["target"],
            "interface": interface, "files": files, "entry_file": entry,
            "editable_files": editable, "public_properties": properties}


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    config = read_json(path)
    if not isinstance(config, dict):
        raise ProtocolError("config must be an object")
    config.setdefault("method", {})
    config.setdefault("provider", {"kind": "replay", "responses": []})
    config.setdefault("validators", {})
    if not all(isinstance(config[k], dict) for k in ("method", "provider", "validators")):
        raise ProtocolError("method/provider/validators must be objects")
    provider = config["provider"]
    if any(key in provider for key in ("api_key", "token", "password", "headers")):
        raise ProtocolError("provider credentials must use api_key_env, never literal config values")
    if "base_url" in provider:
        url = urlsplit(provider["base_url"])
        if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ProtocolError("base_url must be an HTTP(S) URL without embedded credentials/query")
    if "path" in provider:
        provider["path"] = str((path.parent / provider["path"]).resolve())
    for name in ("cases", "apis", "documents", "patches"):
        key = name + "_path"
        if key in config["method"]:
            if name in config["method"]:
                raise ProtocolError(f"choose {name} or {key}, not both")
            records = read_json((path.parent / config["method"][key]).resolve())
            if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
                raise ProtocolError(f"{key} must contain a JSON array of records")
            config["method"][name] = records
    config["config_directory"] = str(path.parent)
    return config
