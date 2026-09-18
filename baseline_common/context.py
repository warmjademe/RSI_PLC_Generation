from __future__ import annotations

import copy
import os
from pathlib import Path

from .budget import Budget
from .errors import ProtocolError, ModelResponseError
from .providers import create_provider
from .utils import Redactor, canonical_json, content_hash, object_hash, parse_json_object, safe_relative, validate_files
from .validators import run_validator


class RunContext:
    def __init__(self, task: dict, config: dict, output: str | Path, *, method: str = "unknown", provider=None):
        self.task = copy.deepcopy(task)
        self.config = copy.deepcopy(config)
        self.method = method
        self.output = Path(output).resolve()
        self.provider = provider or create_provider(config.get("provider", {}))
        self.redactor = Redactor(getattr(self.provider, "secrets", []))
        self.budget = Budget(config.get("budgets"), candidate_limit=config.get('protocol_candidate_limit', 5))
        self._receipts: dict[str, dict] = {}
        self._candidate_pending = False
        self._candidate_project_hash = None
        self._candidate_history: list[dict] = []
        self._events = 0
        self.latest_code = task.get("files", {}).get(task["entry_file"], "")
        self.latest_files = dict(task.get("files", {}))
        self.finished = False
        self.output.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.write_artifact("task.json", self.task)
        self.write_artifact("run_config.json", {
            "method": method, "provider_kind": self.provider.kind,
            "requested_model": self.provider.requested_model,
            "sampling": {k: config.get("provider", {}).get(k) for k in ("temperature", "top_p", "seed")},
            "method_config": config.get("method", {}), "budgets": self.budget.limits,
            "validator_configuration": config.get("validators", {}),
            "task_hash": object_hash(self.task), "config_hash": object_hash(self.redactor.clean(config)),
        })
        self.record("run_started", method=method, task_id=task["id"])

    def write_artifact(self, name: str, value) -> None:
        name = safe_relative(name)
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or not path.resolve().is_relative_to(self.output):
            raise ProtocolError("artifact path escapes run directory")
        if isinstance(value, str):
            text = self.redactor.text(value)
        else:
            text = canonical_json(self.redactor.clean(value)) + "\n"
        path.write_text(text, encoding="utf-8")

    def record(self, event: str, **data) -> None:
        self._events += 1
        item = self.redactor.clean({"sequence": self._events, "event": event, **data})
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(item) + "\n")

    def ask(self, role: str, system: str, payload: dict, *, output_tokens: int | None = None) -> dict:
        if not isinstance(payload, dict) or not isinstance(system, str):
            raise ProtocolError("model system/payload have invalid types")
        request = {"role": role, "system": system, "payload": payload}
        if self.redactor.clean(request) != request:
            raise ProtocolError("a recognizable credential was found in model context")
        reservation = self.budget.before_model(canonical_json(request), output_limit=output_tokens)
        name = f"model/{self.budget.model_calls:04d}"
        self.write_artifact(name + "/request.json", request)
        self.record("model_requested", role=role, call=self.budget.model_calls)
        settled = False
        try:
            result = self.provider.complete(role, system, payload, max_tokens=reservation[1],
                                            timeout=self.budget.remaining_seconds())
            self.write_artifact(name + "/response.json", result)
            settled = True
            self.budget.settle_model(reservation, result.get("usage"))
            if result.get("invalid_finish"):
                raise ModelResponseError("model response was truncated or did not finish normally")
            try:
                parsed = parse_json_object(result["text"])
            except (ProtocolError, ValueError, TypeError) as exc:
                raise ModelResponseError("model did not return the required JSON object") from exc
            self.record("model_completed", role=role, call=self.budget.model_calls,
                        resolved_model=result.get("model"), usage=result.get("usage"))
            return parsed
        except Exception as exc:
            if not settled:
                # A failed request consumed an attempt; conservative token charge is explicit.
                try:
                    self.budget.settle_model(reservation, None)
                except Exception:
                    pass
            self.write_artifact(name + "/error.json", {"type": type(exc).__name__, "message": str(exc)})
            self.record("model_failed", role=role, call=self.budget.model_calls, error_type=type(exc).__name__)
            raise

    def _project(self, code: str, files: dict | None) -> dict[str, str]:
        if not isinstance(code, str):
            raise ProtocolError("candidate code must be a string")
        original = self.task.get("files", {})
        if files is None:
            current = dict(original)
            current[self.task["entry_file"]] = code
        else:
            current = validate_files(files)
        if current.get(self.task["entry_file"]) != code:
            raise ProtocolError("entry_file content does not match candidate code")
        editable = set(self.task["editable_files"])
        for name in set(original) | set(current):
            if name not in editable and original.get(name) != current.get(name):
                raise ProtocolError("candidate changed a non-editable project file")
        if self.redactor.clean(current) != current:
            raise ProtocolError("candidate project contains a recognizable credential")
        return validate_files(current)

    def checkpoint(self, code: str, *, files: dict | None = None) -> None:
        """Persist the latest candidate without claiming any validation result."""
        current = self._project(code, files)
        self.latest_code, self.latest_files = code, copy.deepcopy(current)
        self.write_artifact("latest_candidate.json", {"code": code, "files": current,
                            "code_hash": content_hash(code), "project_hash": object_hash(current),
                            "validation_status": "unchecked_checkpoint"})

    @property
    def remaining_candidates(self) -> int:
        return self.budget.limits["max_candidates"] - self.budget.candidates

    def begin_candidate(self, reason: str = "generation") -> int:
        """Reserve a candidate attempt before generation or the next edit phase."""
        candidate_id = self.budget.before_candidate()
        self._candidate_pending = True
        self._candidate_project_hash = None
        self._candidate_history.append({"candidate_id": candidate_id, "reason": reason,
                                        "model_calls_before": self.budget.model_calls,
                                        "tool_calls_before": self.budget.tool_calls})
        self.record("candidate_started", candidate_id=candidate_id, reason=reason,
                    remaining_candidates=self.remaining_candidates)
        return candidate_id

    def check(self, stage: str, code: str, *, files: dict | None = None,
              properties: list | None = None, plan: dict | None = None) -> dict:
        if stage not in ("compile", "formal", "specification", "runtime"):
            raise ProtocolError("unsupported verification stage")
        if properties is not None and not isinstance(properties, list):
            raise ProtocolError("properties must be a list")
        if plan is not None and not isinstance(plan, dict):
            raise ProtocolError("plan must be an object")
        current = self._project(code, files)
        project_hash = object_hash(current)
        if self.budget.candidates == 0 or (not self._candidate_pending and self._candidate_project_hash != project_hash):
            self.begin_candidate("first_submission" if self.budget.candidates == 0 else "new_project_submission")
        self._candidate_pending = False
        self._candidate_project_hash = project_hash
        self.checkpoint(code, files=current)
        self.budget.before_tool()
        check_id = f"check-{self.budget.tool_calls:04d}"
        directory_name = f"checks/{self.budget.tool_calls:04d}_{stage}"
        request = {"schema_version": 1, "check_id": check_id, "candidate_id": self.budget.candidates, "stage": stage,
                   "task": self.task, "target": self.task["target"], "code": code,
                   "entry_file": self.task["entry_file"], "files": current,
                   "properties": properties, "plan": plan, "code_hash": content_hash(code),
                   "project_hash": object_hash(current),
                   "plan_hash": object_hash(plan) if plan is not None else None,
                   "properties_hash": object_hash(properties) if properties is not None else None}
        self.write_artifact(directory_name + "/request.json", request)
        self.record("check_requested", check_id=check_id, stage=stage,
                    code_hash=request["code_hash"], project_hash=request["project_hash"])
        config = self.config.get("validators", {}).get(stage, {"kind": "unavailable"})
        if not isinstance(config, dict):
            raise ProtocolError("validator configuration must be an object")
        try:
            raw = run_validator(config, request, self.output / directory_name,
                                timeout=self.budget.remaining_seconds(), redactor=self.redactor)
            self.budget.remaining_seconds()
        except Exception as exc:
            self.write_artifact(directory_name + "/error.json", {"type": type(exc).__name__, "message": str(exc)})
            self.record("check_error", check_id=check_id, stage=stage, error_type=type(exc).__name__)
            raise
        if raw["status"] == "pass" and not raw.get("evidence", {}).get("executed"):
            raise ProtocolError("pass requires an executed validator")
        receipt = {"check_id": check_id, "candidate_id": self.budget.candidates, "stage": stage, "status": raw["status"],
                   "diagnostics": self.redactor.clean(raw.get("diagnostics", [])),
                   **{key: request[key] for key in ("code_hash", "project_hash", "plan_hash", "properties_hash")},
                   "evidence": self.redactor.clean({**raw.get("evidence", {}), "artifact_directory": directory_name})}
        self._receipts[check_id] = copy.deepcopy(receipt)
        self.write_artifact(directory_name + "/receipt.json", receipt)
        self.record("check_completed", **receipt)
        return copy.deepcopy(receipt)

    def finish(self, code: str, status: str, reason: str, *, files: dict | None = None,
               checks: list | None = None, details: dict | None = None) -> dict:
        if self.finished:
            raise ProtocolError("run already finished")
        if status not in ("passed", "failed", "incomplete"):
            raise ProtocolError("invalid final status")
        current = self._project(code, files)
        checks = checks or []
        for check in checks:
            if not isinstance(check, dict) or self._receipts.get(check.get("check_id")) != check:
                raise ProtocolError("final check is not an authentic unmodified receipt from this run")
            if check["code_hash"] != content_hash(code) or check["project_hash"] != object_hash(current):
                raise ProtocolError("final checks belong to a different program/project version")
            if check["candidate_id"] != self.budget.candidates:
                raise ProtocolError("final checks belong to a previous candidate attempt")
        if status == "passed" and (not code.strip() or not checks or any(check["status"] != "pass" for check in checks)):
            raise ProtocolError("passing delivery requires nonempty code and passing tool receipts")
        if status == "passed":
            for check in checks:
                same_stage = [r for r in self._receipts.values() if r["stage"] == check["stage"]]
                if same_stage[-1] != check:
                    raise ProtocolError("final delivery uses a superseded stage receipt")
        budget = self.budget.report()
        candidate_results = []
        for candidate in self._candidate_history:
            history = [r for r in self._receipts.values() if r["candidate_id"] == candidate["candidate_id"]]
            candidate_results.append({**candidate,
                "checks": [{k: r[k] for k in ("check_id", "stage", "status", "code_hash", "project_hash")} for r in history],
                "last_status_by_stage": {r["stage"]: r["status"] for r in history},
                "submitted_to_tools": bool(history)})
        rates = self.config.get("cost_per_million_tokens", {})
        cost = None
        if "input" in rates and "output" in rates:
            cost = (budget["input_tokens"] * float(rates["input"]) + budget["output_tokens"] * float(rates["output"])) / 1e6
        mode = "replay" if self.provider.kind == "replay" else "test_tools" if any(c["evidence"].get("test_only") for c in checks) else "live"
        result = {"schema_version": 1, "method": self.method, "implementation": "source_mapped_reimplementation",
                  "task_id": self.task["id"], "target": self.task["target"], "status": status, "reason": reason,
                  "code": code, "files": current, "code_hash": content_hash(code), "project_hash": object_hash(current),
                  "checks": checks, "details": details or {}, "budget": budget,
                  "candidate_results": candidate_results,
                  "evidence_mode": mode, "benchmark_score": None,
                  "estimated_cost_usd": cost, "cost_rates": rates or None,
                  "requested_model": self.provider.requested_model}
        self.write_artifact("candidate.st", code)
        self.write_artifact("project_files.json", current)
        self.write_artifact("result.json", result)
        self.record("run_finished", status=status, reason=reason, evidence_mode=mode, budget=budget)
        self.finished = True
        return result
