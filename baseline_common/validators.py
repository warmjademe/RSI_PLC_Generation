"""Run configured verification tools in per-invocation workspace snapshots."""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import time
from pathlib import Path

from .errors import ProtocolError
from .utils import canonical_json, content_hash, parse_json_object


def run_validator(config: dict, request: dict, directory: Path, *, timeout: float, redactor) -> dict:
    kind = config.get("kind", "unavailable")
    if kind == "unavailable":
        return {"status": "unknown", "diagnostics": ["No validator configured for this stage."],
                "evidence": {"kind": "unavailable", "executed": False}}
    if kind != "command":
        raise ProtocolError("validator kind must be command or unavailable")
    command = config.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ProtocolError("validator command must be a nonempty argv list; shell strings are not accepted")
    protocol = config.get("protocol", "json")
    if protocol not in ("json", "exit_code", "semaplc"):
        raise ProtocolError("validator protocol must be json, exit_code, or semaplc")
    if protocol == "exit_code" and request["stage"] != "compile":
        raise ProtocolError("exit code alone can establish compilation only, not behavioral correctness")
    configured_timeout = config.get("timeout_seconds", 120)
    if (isinstance(configured_timeout, bool) or not isinstance(configured_timeout, (int, float))
            or not math.isfinite(configured_timeout) or configured_timeout <= 0):
        raise ProtocolError("validator timeout_seconds must be a positive finite number")
    execution_timeout = min(timeout, configured_timeout)
    if not math.isfinite(execution_timeout) or execution_timeout <= 0:
        raise ProtocolError("validator has no positive execution time remaining")
    workspace = directory / "workspace"
    workspace.mkdir()
    for name, text in request["files"].items():
        dest = workspace / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    source = workspace / request["entry_file"]
    substitutions = {"{workspace}": str(workspace), "{source}": str(source),
                     "{request}": str(directory / "request.json"),
                     "{result}": str(directory / "tool_result.json"),
                     "{plan}": str(directory / "plan.json"), "{python}": __import__("sys").executable}
    if request.get("plan") is not None:
        (directory / "plan.json").write_text(canonical_json(request["plan"]), encoding="utf-8")
    # Replace known placeholders only, leaving compiler braces and flags intact.
    argv = []
    for argument in command:
        for marker, value in substitutions.items():
            argument = argument.replace(marker, value)
        argv.append(argument)
    env = os.environ.copy()
    extra_env = config.get("env", {})
    if not isinstance(extra_env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extra_env.items()):
        raise ProtocolError("validator env must be string names and values")
    env.update(extra_env)
    started = time.monotonic()
    raw_stdout, raw_stderr = directory / ".stdout.raw", directory / ".stderr.raw"
    returned = None
    timed_out = False
    launch_error = None
    try:
        with raw_stdout.open("wb") as out, raw_stderr.open("wb") as err:
            try:
                process = subprocess.Popen(argv, cwd=workspace, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                           env=env, start_new_session=True)
                try:
                    process.communicate(canonical_json(request).encode(), timeout=execution_timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate()
                except BaseException:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate()
                    raise
                returned = process.returncode
            except OSError as exc:
                launch_error = type(exc).__name__
        limit = 4 * 1024 * 1024
        oversized = raw_stdout.stat().st_size > limit or raw_stderr.stat().st_size > limit
        with raw_stdout.open("rb") as handle:
            stdout = handle.read(limit).decode("utf-8", errors="replace")
        with raw_stderr.open("rb") as handle:
            stderr = handle.read(limit).decode("utf-8", errors="replace")
    finally:
        # Raw temporary streams are replaced with redacted permanent artifacts.
        raw_stdout.unlink(missing_ok=True)
        raw_stderr.unlink(missing_ok=True)
    (directory / "stdout.txt").write_text(redactor.text(stdout), encoding="utf-8")
    (directory / "stderr.txt").write_text(redactor.text(stderr), encoding="utf-8")
    evidence = {"kind": "command", "executed": launch_error is None, "returncode": returned,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "tool_name": config.get("name", Path(argv[0]).name), "protocol": protocol,
                "test_only": bool(config.get("test_only", False)),
                "stdout_artifact": "stdout.txt", "stderr_artifact": "stderr.txt"}
    if launch_error or timed_out or oversized:
        return {"status": "error", "diagnostics": [launch_error or ("validator timeout" if timed_out else "tool output too large")], "evidence": evidence}
    immutable_json = {directory / "request.json": request}
    if request.get("plan") is not None:
        immutable_json[directory / "plan.json"] = request["plan"]
    for path, expected in immutable_json.items():
        try:
            if path.is_symlink() or json.loads(path.read_text(encoding="utf-8")) != expected:
                raise ValueError("changed")
        except (OSError, ValueError, UnicodeError):
            return {"status": "error", "diagnostics": ["validator modified the request or test plan"], "evidence": evidence}
    # A tool may generate build artifacts, but changing the source invalidates its receipt.
    for name, expected in request["files"].items():
        p = workspace / name
        try:
            matches = (p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(workspace.resolve())
                       and p.read_text(encoding="utf-8") == expected)
        except (OSError, UnicodeError):
            matches = False
        if not matches:
            return {"status": "error", "diagnostics": ["validator modified an input project file"], "evidence": evidence}
    if protocol == "exit_code":
        status = "pass" if returned == 0 else "error" if returned in config.get("infrastructure_exit_codes", [124, 126, 127]) or returned < 0 else "fail"
        return {"status": status, "diagnostics": [stderr or stdout] if status != "pass" else [], "evidence": evidence}
    try:
        if config.get("result_file", False):
            p = directory / "tool_result.json"
            if not p.is_file() or p.is_symlink() or p.stat().st_size > limit:
                raise ProtocolError("missing or invalid tool result file")
            result = parse_json_object(p.read_text(encoding="utf-8"))
            p.write_text(canonical_json(redactor.clean(result)), encoding="utf-8")
        else:
            result = parse_json_object(stdout)
        if protocol == "semaplc":
            if result.get("stHash") != request["code_hash"]:
                raise ProtocolError("SemaPLC envelope source hash does not match this candidate")
            for key in ("project_hash", "plan_hash", "properties_hash"):
                if key in result and result[key] != request[key]:
                    raise ProtocolError(f"SemaPLC envelope {key} mismatch")
            status = "pass" if result.get("ok") is True else "fail" if result.get("ok") is False else "unknown"
            result = {"status": status, "diagnostics": [result.get("failure") or result.get("summary", "")],
                      "evidence": {"semaplc_envelope": result}}
        if result.get("status") not in ("pass", "fail", "unknown", "error"):
            raise ProtocolError("validator must return status pass/fail/unknown/error")
        if "stage" in result and result["stage"] != request["stage"]:
            raise ProtocolError("validator stage mismatch")
        for key in ("code_hash", "project_hash", "plan_hash", "properties_hash"):
            if key in result and result[key] != request[key]:
                raise ProtocolError(f"validator {key} mismatch")
        diagnostics = result.get("diagnostics", [])
        if isinstance(diagnostics, str):
            diagnostics = [diagnostics]
        if not isinstance(diagnostics, list) or not isinstance(result.get("evidence", {}), dict):
            raise ProtocolError("validator diagnostics/evidence have invalid types")
        evidence["tool_report"] = result.get("evidence", {})
        if returned != 0 and result["status"] == "pass":
            raise ProtocolError("nonzero tool exit cannot provide a passing receipt")
        return {"status": result["status"], "diagnostics": diagnostics, "evidence": evidence}
    except (ProtocolError, OSError, UnicodeError) as exc:
        return {"status": "error", "diagnostics": [str(exc)], "evidence": evidence}
