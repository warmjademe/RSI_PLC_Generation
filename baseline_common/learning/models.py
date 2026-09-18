from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AttemptStep:
    number: int
    repair_mode: str
    hypothesis: str
    candidate_st: str
    candidate_ld: dict[str, Any] | None
    raw_response_text: str
    feedback: tuple[dict[str, Any], ...]
    usage: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrajectoryEpisode:
    task_id: str
    run_key: str
    target: str
    output_language: str
    category_id: str
    category: str
    semantic_signature: str
    requirement: str
    interface: str
    public_metadata: dict[str, Any]
    retrieval_text: str
    attempts: tuple[AttemptStep, ...]
    success: bool
    terminal_status: str
    reward: float
    provenance: dict[str, str]

    @property
    def scope(self) -> tuple[str, str]:
        return self.target, self.output_language

    def state_text(self) -> str:
        return "\n".join((
            f"PLC target: {self.target}",
            f"Output language: {self.output_language}",
            f"Category: {self.category_id} {self.category}",
            f"Semantic signature: {self.semantic_signature}",
            "Requirement:",
            self.requirement,
            "Interface:",
            self.interface,
            "Retrieval concepts:",
            self.retrieval_text,
        ))

    def compact_trace(self, *, maximum_candidate_chars: int = 6000) -> dict[str, Any]:
        return {
            "task": {
                "target": self.target,
                "output_language": self.output_language,
                "category": self.category,
                "semantic_signature": self.semantic_signature,
                "requirement": self.requirement,
                "interface": self.interface,
            },
            "attempts": [
                {
                    "number": attempt.number,
                    "repair_mode": attempt.repair_mode,
                    "hypothesis": attempt.hypothesis,
                    "candidate_st": attempt.candidate_st[:maximum_candidate_chars],
                    "candidate_ld": attempt.candidate_ld,
                    "raw_response_text": (
                        attempt.raw_response_text[:maximum_candidate_chars]
                        if not attempt.candidate_st.strip() else ""
                    ),
                    "feedback": list(attempt.feedback),
                }
                for attempt in self.attempts
            ],
            "result": {
                "success": self.success,
                "terminal_status": self.terminal_status,
                "reward": self.reward,
            },
        }


@dataclass(frozen=True)
class TaskQuery:
    task_id: str
    target: str
    output_language: str
    category_id: str
    category: str
    semantic_signature: str
    requirement: str
    interface: str
    public_metadata: dict[str, Any]
    retrieval_text: str

    @property
    def scope(self) -> tuple[str, str]:
        return self.target, self.output_language

    def state_text(self) -> str:
        return "\n".join((
            f"PLC target: {self.target}",
            f"Output language: {self.output_language}",
            f"Category: {self.category_id} {self.category}",
            f"Semantic signature: {self.semantic_signature}",
            "Requirement:",
            self.requirement,
            "Interface:",
            self.interface,
            "Retrieval concepts:",
            self.retrieval_text,
        ))


@dataclass(frozen=True)
class MemoryContext:
    method: str
    text: str
    memory_item_ids: tuple[str, ...] = ()
    skill_ids: tuple[str, ...] = ()
    retrieval_usage: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def memory_items_injected(self) -> int:
        return len(self.memory_item_ids)

    @property
    def skills_injected(self) -> int:
        return len(self.skill_ids)
