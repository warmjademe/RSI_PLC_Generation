from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


OracleStatus = Literal["pass", "fail", "inconclusive"]


@dataclass(frozen=True)
class Task:
    task_id: str
    split: str
    public_dir: Path
    oracle_dir: Path | None
    requirement: str
    interface: str
    metadata: dict[str, Any]

    @property
    def retrieval_text(self) -> str:
        retrieval = self.metadata.get("retrieval", {})
        scope = self.metadata.get("evaluation_scope", {})
        values = [
            self.metadata.get("category", ""),
            *self.metadata.get("iec_features", []),
            *retrieval.get("control_patterns", []),
            *retrieval.get("ontology_concepts", []),
            *retrieval.get("hazards", []),
            (
                f"plc_target.{scope.get('target')}"
                if scope.get("target") else ""
            ),
            (
                f"output_language.{scope.get('output_language')}"
                if scope.get("output_language") else ""
            ),
        ]
        return " ".join(str(value) for value in values if value)

    @property
    def signature(self) -> str:
        retrieval = self.metadata.get("retrieval", {})
        patterns = sorted(retrieval.get("control_patterns", []))
        types = sorted({
            item.get("type", "")
            for side in ("inputs", "outputs")
            for item in self.metadata.get("interface", {}).get(side, [])
        })
        category = self.metadata.get("category_id", "unknown")
        scope = self.metadata.get("evaluation_scope", {})
        return "|".join([
            category,
            ",".join(patterns),
            ",".join(types),
            f"target={scope.get('target', 'unspecified')}",
            f"language={scope.get('output_language', 'unspecified')}",
        ])


def with_evaluation_scope(
    task: Task,
    *,
    target: str,
    output_language: str,
) -> Task:
    if target not in {"DVP48ES300R", "AS228T-A"}:
        raise ValueError(f"unsupported PLC target scope: {target}")
    if output_language not in {"st", "ld"}:
        raise ValueError(f"unsupported output-language scope: {output_language}")
    metadata = dict(task.metadata)
    metadata["evaluation_scope"] = {
        "target": target,
        "output_language": output_language,
    }
    return Task(
        task_id=task.task_id,
        split=task.split,
        public_dir=task.public_dir,
        oracle_dir=task.oracle_dir,
        requirement=task.requirement,
        interface=task.interface,
        metadata=metadata,
    )


@dataclass
class ModelReply:
    text: str
    provider: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    request_id: str | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class OracleResult:
    status: OracleStatus
    summary: str
    evidence: list[dict[str, Any]]
    role: str
    worker_id: str | None = None
    job_id: str | None = None
    tool_version: str | None = None
    elapsed_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def actionable_feedback(self) -> str:
        if self.status == "pass":
            return "Oracle passed."
        lines = [self.summary]
        for item in self.evidence[:8]:
            summary = item.get("summary")
            if summary:
                lines.append(f"- {summary}")
            trace = item.get("trace", {})
            for failure in trace.get("failures", [])[:4]:
                lines.append(f"  mismatch: {failure}")
        return "\n".join(lines)


@dataclass
class RetrievedKnowledge:
    skills: list[dict[str, Any]] = field(default_factory=list)
    policies: list[dict[str, Any]] = field(default_factory=list)
    traces: list[dict[str, Any]] = field(default_factory=list)
    cognition: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AttemptOutcome:
    attempt_index: int
    candidate: str
    feedback_result: OracleResult
    sealed_result: OracleResult | None
    reply: ModelReply
    policy_ids: list[int]
    skill_ids: list[int]
    reflection: dict[str, Any]

    @property
    def terminal_status(self) -> OracleStatus:
        if self.feedback_result.status != "pass":
            return self.feedback_result.status
        return self.sealed_result.status if self.sealed_result else "pass"
