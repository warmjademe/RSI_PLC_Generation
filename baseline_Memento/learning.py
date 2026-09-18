from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from baseline_common.learning.artifacts import (
    read_jsonl,
    seal_manifest,
    verify_manifest,
    write_jsonl,
)
from baseline_common.learning.embeddings import DenseIndex, Embedder
from baseline_common.learning.models import MemoryContext, TaskQuery, TrajectoryEpisode
from baseline_common.learning.protocol import EvaluationProtocol, canonical, digest


class MementoError(RuntimeError):
    pass


@dataclass(frozen=True)
class MementoCase:
    case_id: str
    state: str
    action: dict[str, Any]
    reward: float
    target: str
    output_language: str
    source_task_id: str
    provenance: dict[str, str]

    @classmethod
    def from_episode(cls, episode: TrajectoryEpisode) -> "MementoCase":
        # The paper stores only the final trajectory state/action/reward tuple.
        # In PLC generation, the final action is the bounded candidate/feedback
        # sequence produced for that task; it is kept in its original form.
        action = {
            "attempts": [attempt.to_dict() for attempt in episode.attempts],
            "terminal_status": episode.terminal_status,
        }
        identifier = digest({
            "method": "memento_nonparametric",
            "source_task_id": episode.task_id,
            "run_key": episode.run_key,
        })[:24]
        return cls(
            case_id=f"case_{identifier}",
            state=episode.state_text(),
            action=action,
            reward=episode.reward,
            target=episode.target,
            output_language=episode.output_language,
            source_task_id=episode.task_id,
            provenance=dict(episode.provenance),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "case_id": self.case_id,
            "state": self.state,
            "action": self.action,
            "reward": self.reward,
            "target": self.target,
            "output_language": self.output_language,
            "source_task_id": self.source_task_id,
            "provenance": self.provenance,
        }


class MementoTrainer:
    method = "memento_nonparametric"
    top_k = 4

    def __init__(self, *, protocol: EvaluationProtocol, embedder: Embedder):
        self.protocol = protocol
        self.embedder = embedder

    def train_and_freeze(
        self,
        episodes: Iterable[TrajectoryEpisode],
        *,
        output_root: Path,
        corpus_binding: dict[str, Any],
        source_revision: str,
    ) -> dict[str, Any]:
        if output_root.exists():
            raise MementoError("Memento output root already exists")
        output_root.mkdir(parents=True)
        cases = tuple(
            MementoCase.from_episode(episode)
            for episode in sorted(episodes, key=lambda item: item.task_id)
        )
        if len(cases) != 1000 or len({case.case_id for case in cases}) != 1000:
            raise MementoError("Memento requires the common 1000-case corpus")
        write_jsonl(output_root / "cases.jsonl", (case.to_dict() for case in cases))
        index = DenseIndex.build(
            item_ids=[case.case_id for case in cases],
            texts=[case.state for case in cases],
            metadata=[{
                "target": case.target,
                "output_language": case.output_language,
                "reward": case.reward,
                "source_task_id": case.source_task_id,
            } for case in cases],
            embedder=self.embedder,
        )
        index.save(output_root / "dense_index")
        return seal_manifest(output_root, {
            "schema_version": 1,
            "method": self.method,
            "method_variant": "nonparametric_cbr",
            "paper_equations": [12, 13],
            "top_k": self.top_k,
            "state_encoder": {
                "name": self.embedder.model_name,
                "revision": self.embedder.revision,
                "frozen": True,
                "similarity": "cosine",
            },
            "case_count": len(cases),
            "success_case_count": sum(case.reward == 1.0 for case in cases),
            "failure_case_count": sum(case.reward == 0.0 for case in cases),
            "corpus_binding": corpus_binding,
            "protocol_sha256": self.protocol.sha256,
            "source_revision": source_revision,
            "test_split_accessed": False,
            "base_model_weights_updated": False,
        })


class FrozenMemento:
    method = "memento_nonparametric"

    def __init__(
        self,
        *,
        root: Path,
        protocol: EvaluationProtocol,
        embedder: Embedder,
    ):
        self.root = root.resolve()
        self.protocol = protocol
        self.manifest = verify_manifest(
            self.root,
            method=self.method,
            protocol_sha256=protocol.sha256,
        )
        self.cases = {
            str(item["case_id"]): item
            for item in read_jsonl(self.root / "cases.jsonl")
        }
        self.index = DenseIndex.load(self.root / "dense_index")
        if (
            self.index.model_name != embedder.model_name
            or self.index.revision != embedder.revision
        ):
            raise MementoError("Memento query encoder differs from training")
        self.embedder = embedder
        if self.manifest.get("top_k") != 4:
            raise MementoError("Memento K drifted from the paper-selected value")

    @staticmethod
    def _prompt_case(case: dict[str, Any], score: float) -> dict[str, Any]:
        attempts = []
        for raw in case.get("action", {}).get("attempts", []):
            attempts.append({
                "number": raw.get("number"),
                "repair_mode": raw.get("repair_mode"),
                "hypothesis": raw.get("hypothesis"),
                "candidate_st": str(raw.get("candidate_st", ""))[:3500],
                "candidate_ld": raw.get("candidate_ld"),
                "raw_response_text": str(raw.get("raw_response_text", ""))[:3500],
                "feedback": raw.get("feedback", []),
            })
        return {
            "case_id": case["case_id"],
            "similarity": round(score, 8),
            "past_state": case["state"],
            "past_action": {
                "attempts": attempts,
                "terminal_status": case.get("action", {}).get(
                    "terminal_status"
                ),
            },
            "past_reward": case["reward"],
        }

    def retrieve(self, query: TaskQuery) -> MemoryContext:
        vectors = self.embedder.encode([query.state_text()])
        if len(vectors) != 1:
            raise MementoError("query encoder returned an invalid batch")
        matches = self.index.search(
            vectors[0], top_k=4, scope=query.scope
        )
        prompt_cases = [
            self._prompt_case(self.cases[match.item_id], match.score)
            for match in matches
        ]
        prefix = (
            "MEMENTO CASE MEMORY (training cases; successes and failures)\n"
            "Adapt similar successful actions. Avoid failure patterns. Do not "
            "copy a case unless its PLC target, output language, interface, and "
            "semantics match. Re-verify every generated program.\n"
        )
        maximum = int(
            self.protocol.document["maximum_memory_context_characters"]
        )
        while prompt_cases:
            text = prefix + json.dumps(
                prompt_cases,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if len(text) <= maximum:
                break
            # Preserve TopK ordering while deterministically shrinking the
            # largest source fields.  Only projection changes; case selection
            # remains Eq. (13).
            changed = False
            for case in reversed(prompt_cases):
                state = case.get("past_state", "")
                if len(state) > 1800:
                    case["past_state"] = state[:1800]
                    changed = True
                for attempt in case.get("past_action", {}).get("attempts", []):
                    candidate = attempt.get("candidate_st", "")
                    if len(candidate) > 1800:
                        attempt["candidate_st"] = candidate[:1800]
                        changed = True
                    raw_response = attempt.get("raw_response_text", "")
                    if len(raw_response) > 1800:
                        attempt["raw_response_text"] = raw_response[:1800]
                        changed = True
            if changed:
                continue
            prompt_cases.pop()
        else:
            text = ""
        return MemoryContext(
            method=self.method,
            text=text,
            memory_item_ids=tuple(
                str(case["case_id"]) for case in prompt_cases
            ),
            metadata={
                "retrieval": "frozen_encoder_cosine_topk",
                "top_k_requested": 4,
                "top_k_injected": len(prompt_cases),
                "scope": list(query.scope),
                "context_sha256": digest(text),
                "context_characters": len(text),
            },
        )
