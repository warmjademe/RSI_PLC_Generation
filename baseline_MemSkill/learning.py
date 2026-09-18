from __future__ import annotations

import copy
import json
import math
import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from baseline_common.learning.artifacts import (
    file_sha256,
    read_jsonl,
    seal_manifest,
    verify_manifest,
    write_json,
    write_jsonl,
)
from baseline_common.learning.corpus import deterministic_development_partition
from baseline_common.learning.embeddings import DenseIndex, Embedder, cosine, normalize
from baseline_common.learning.models import MemoryContext, TaskQuery, TrajectoryEpisode
from baseline_common.learning.protocol import EvaluationProtocol, digest
from baseline_common.learning.provider import ModelCall, LearningProvider, summarize_model_audits


class MemSkillError(RuntimeError):
    pass


@dataclass
class MemoryOperation:
    name: str
    description: str
    instruction: str
    update_type: str
    usage_count: int = 0
    average_reward: float = 0.0
    created_at: str = "initial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "name": self.name,
            "description": self.description,
            "instruction": self.instruction,
            "update_type": self.update_type,
            "usage_count": self.usage_count,
            "average_reward": self.average_reward,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MemoryOperation":
        if value.get("schema_version") != 1 or value.get("update_type") not in {
            "insert", "update", "delete", "noop"
        }:
            raise MemSkillError("invalid MemSkill operation")
        for key in ("name", "description", "instruction"):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise MemSkillError(f"MemSkill operation omitted {key}")
        return cls(
            name=str(value["name"]),
            description=str(value["description"]),
            instruction=str(value["instruction"]),
            update_type=str(value["update_type"]),
            usage_count=int(value.get("usage_count", 0)),
            average_reward=float(value.get("average_reward", 0.0)),
            created_at=str(value.get("created_at", "unknown")),
        )

    def update_reward(self, reward: float) -> None:
        self.usage_count += 1
        self.average_reward += (reward - self.average_reward) / self.usage_count


def _initial_operations() -> dict[str, MemoryOperation]:
    return {
        "insert": MemoryOperation(
            name="insert",
            description=(
                "Memory management skill for capturing new, durable facts from "
                "the current trajectory that are not already in memory."
            ),
            instruction=(
                "Insert concise, distinct and evidence-grounded PLC generation "
                "facts. Avoid duplicates and speculative claims."
            ),
            update_type="insert",
        ),
        "update": MemoryOperation(
            name="update",
            description=(
                "Memory management skill for revising an existing memory when "
                "the trajectory corrects or extends it."
            ),
            instruction=(
                "Update exactly one retrieved memory, retaining accurate details "
                "and grounding every change in the new trajectory."
            ),
            update_type="update",
        ),
        "delete": MemoryOperation(
            name="delete",
            description=(
                "Memory management skill for removing a memory item that is "
                "explicitly contradicted, invalid, or superseded."
            ),
            instruction=(
                "Delete only a retrieved memory whose invalidity is explicit; "
                "when uncertain, do not delete."
            ),
            update_type="delete",
        ),
        "noop": MemoryOperation(
            name="noop",
            description="Memory management skill for making no memory change.",
            instruction=(
                "Use NOOP only when the trajectory provides no durable new, "
                "corrective, or invalidating knowledge."
            ),
            update_type="noop",
        ),
    }


@dataclass
class MemSkillItem:
    memory_id: str
    content: str
    target: str
    output_language: str
    category_id: str
    semantic_signature: str
    source_task_id: str
    source_reward: float
    operation_history: list[str]
    content_history: list[str]
    created_step: int
    last_updated_step: int

    @property
    def scope(self) -> tuple[str, str]:
        return self.target, self.output_language

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "memory_id": self.memory_id,
            "content": self.content,
            "target": self.target,
            "output_language": self.output_language,
            "category_id": self.category_id,
            "semantic_signature": self.semantic_signature,
            "source_task_id": self.source_task_id,
            "source_reward": self.source_reward,
            "operation_history": list(self.operation_history),
            "content_history": list(self.content_history),
            "created_step": self.created_step,
            "last_updated_step": self.last_updated_step,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MemSkillItem":
        if value.get("schema_version") != 1:
            raise MemSkillError("invalid MemSkill memory schema")
        if not isinstance(value.get("content"), str) or not value["content"].strip():
            raise MemSkillError("MemSkill memory is empty")
        return cls(
            memory_id=str(value["memory_id"]),
            content=str(value["content"]),
            target=str(value["target"]),
            output_language=str(value["output_language"]),
            category_id=str(value.get("category_id", "unknown")),
            semantic_signature=str(value.get("semantic_signature", "")),
            source_task_id=str(value.get("source_task_id", "")),
            source_reward=float(value.get("source_reward", 0.0)),
            operation_history=[str(item) for item in value.get("operation_history", [])],
            content_history=[str(item) for item in value.get("content_history", [])],
            created_step=int(value.get("created_step", 0)),
            last_updated_step=int(value.get("last_updated_step", 0)),
        )


def _call_audit(call: ModelCall, *, phase: str, step: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "phase": phase,
        "step": step,
        "provider": call.provider,
        "requested_model": call.requested_model,
        "resolved_model": call.resolved_model,
        "usage": call.usage,
        "latency_seconds": call.latency_seconds,
        "provider_request_count": call.provider_request_count,
        "resolved_models": list(call.resolved_models or (call.resolved_model,)),
        "content_sha256": digest(call.content),
    }


def _top_matches(
    vector: Sequence[float],
    memories: Sequence[MemSkillItem],
    memory_vectors: Sequence[Sequence[float]],
    *,
    scope: tuple[str, str],
    top_k: int,
) -> list[tuple[int, float]]:
    matches = [
        (index, cosine(vector, candidate))
        for index, (memory, candidate) in enumerate(zip(memories, memory_vectors))
        if memory.scope == scope
    ]
    return sorted(matches, key=lambda item: (-item[1], memories[item[0]].memory_id))[:top_k]


class TorchPPOController:
    """Lazy PyTorch implementation of MemSkill's dual-encoder PPO controller."""

    def __init__(
        self,
        *,
        state_dim: int,
        operation_dim: int,
        device: str,
        seed: int,
        action_top_k: int = 3,
    ):
        try:
            import numpy as np
            import torch
            import torch.nn as nn
        except ImportError as exc:
            raise MemSkillError(
                "formal MemSkill training requires numpy and torch"
            ) from exc
        torch.manual_seed(seed)
        if str(device).startswith("cuda"):
            torch.cuda.manual_seed_all(seed)

        class ActorCritic(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                hidden = 256
                self.state_net = nn.Sequential(
                    nn.Linear(state_dim, hidden), nn.ReLU(),
                    nn.Linear(hidden, hidden), nn.ReLU(),
                )
                self.operation_net = nn.Sequential(
                    nn.Linear(operation_dim, hidden), nn.ReLU(),
                    nn.Linear(hidden, hidden), nn.ReLU(),
                )
                self.actor_head = nn.Sequential(
                    nn.Linear(hidden * 2, hidden), nn.ReLU(), nn.Linear(hidden, 1)
                )
                self.critic_head = nn.Sequential(
                    nn.Linear(hidden, hidden), nn.ReLU(), nn.Linear(hidden, 1)
                )

            def logits_and_value(self, state, operations, mask=None):
                state_h = self.state_net(state)
                operation_h = self.operation_net(operations)
                expanded = state_h.unsqueeze(1).expand(-1, operation_h.shape[1], -1)
                logits = self.actor_head(
                    torch.cat((expanded, operation_h), dim=-1)
                ).squeeze(-1)
                if mask is not None:
                    logits = logits.masked_fill(mask == 0, float("-inf"))
                return logits, self.critic_head(state_h).squeeze(-1)

        self.np = np
        self.torch = torch
        self.model = ActorCritic().to(device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=1e-4)
        self.device = device
        self.action_top_k = action_top_k
        self.buffer: list[dict[str, Any]] = []
        self.rng = random.Random(seed)

    def _joint_log_probability(self, logits, actions):
        torch = self.torch
        probabilities = torch.softmax(logits, dim=-1)
        selected = torch.gather(probabilities, -1, actions)
        remaining = torch.ones(selected.shape[0], device=selected.device)
        result = torch.zeros_like(remaining)
        for index in range(selected.shape[1]):
            probability = selected[:, index].clamp(min=1e-8)
            result = result + torch.log(probability) - torch.log(remaining.clamp(min=1e-8))
            remaining = (remaining - probability).clamp(min=1e-8)
        return result

    def select(
        self,
        state: Sequence[float],
        operation_vectors: Sequence[Sequence[float]],
        *,
        deterministic: bool,
        new_operation_mask: Sequence[float] | None = None,
        new_action_bias_scale: float = 0.0,
    ) -> tuple[list[int], float, float]:
        torch = self.torch
        state_tensor = torch.tensor([state], dtype=torch.float32, device=self.device)
        operation_tensor = torch.tensor(
            [operation_vectors], dtype=torch.float32, device=self.device
        )
        with torch.no_grad():
            logits, value = self.model.logits_and_value(state_tensor, operation_tensor)
            logits = logits[0]
            if new_operation_mask is not None and new_action_bias_scale > 0:
                new_mask = torch.tensor(
                    new_operation_mask, dtype=torch.float32, device=self.device
                )
                probabilities = torch.softmax(logits, dim=-1)
                new_probability = (probabilities * new_mask).sum()
                if new_probability < 0.30 and new_mask.sum() > 0:
                    delta = torch.log(torch.tensor(0.30, device=self.device) / (new_probability + 1e-8))
                    logits = logits + delta.clamp(0, 3.44) * new_action_bias_scale * new_mask
            k = min(self.action_top_k, len(operation_vectors))
            if deterministic:
                actions = torch.topk(logits, k).indices
            else:
                uniform = torch.rand(logits.shape, device=self.device).clamp(1e-8, 1 - 1e-8)
                gumbel = -torch.log(-torch.log(uniform))
                actions = torch.topk(logits + gumbel, k).indices
            log_probability = self._joint_log_probability(
                logits.unsqueeze(0), actions.unsqueeze(0)
            )[0]
        return actions.tolist(), float(log_probability.item()), float(value[0].item())

    def add_transition(
        self,
        *,
        state: Sequence[float],
        operation_vectors: Sequence[Sequence[float]],
        actions: Sequence[int],
        log_probability: float,
        value: float,
        reward: float,
    ) -> None:
        self.buffer.append({
            "state": list(state),
            "operations": [list(item) for item in operation_vectors],
            "actions": list(actions),
            "old_log_probability": log_probability,
            "old_value": value,
            "reward": reward,
        })

    def update(self, *, epochs: int = 2) -> dict[str, float]:
        if not self.buffer:
            return {}
        torch = self.torch
        np = self.np
        advantages = np.array(
            [item["reward"] - item["old_value"] for item in self.buffer],
            dtype=np.float32,
        )
        returns = np.array([item["reward"] for item in self.buffer], dtype=np.float32)
        if len(advantages) > 1 and float(advantages.std()) > 1e-8:
            advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        losses: list[float] = []
        for _ in range(epochs):
            indices = list(range(len(self.buffer)))
            self.rng.shuffle(indices)
            for index in indices:
                item = self.buffer[index]
                state = torch.tensor([item["state"]], dtype=torch.float32, device=self.device)
                operations = torch.tensor(
                    [item["operations"]], dtype=torch.float32, device=self.device
                )
                actions = torch.tensor(
                    [item["actions"]], dtype=torch.long, device=self.device
                )
                logits, value = self.model.logits_and_value(state, operations)
                log_probability = self._joint_log_probability(logits, actions)[0]
                ratio = torch.exp(
                    log_probability - torch.tensor(
                        item["old_log_probability"], device=self.device
                    )
                )
                advantage = torch.tensor(float(advantages[index]), device=self.device)
                unclipped = ratio * advantage
                clipped = torch.clamp(ratio, 0.8, 1.2) * advantage
                policy_loss = -torch.min(unclipped, clipped)
                value_target = torch.tensor(float(returns[index]), device=self.device)
                value_loss = 0.5 * (value[0] - value_target).pow(2)
                probabilities = torch.softmax(logits, dim=-1)
                entropy = -(probabilities * torch.log(probabilities.clamp(min=1e-8))).sum()
                loss = policy_loss + 0.5 * value_loss - 0.01 * entropy
                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 0.5)
                self.optimizer.step()
                losses.append(float(loss.item()))
        count = len(self.buffer)
        self.buffer = []
        return {"ppo_loss": sum(losses) / len(losses), "transitions": float(count)}

    def save(self, path: Path) -> None:
        self.torch.save(self.model.state_dict(), path)

    def save_recovery(self, path: Path, *, step: int) -> None:
        """Persist the complete PPO state needed for an exact continuation."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        cuda_rng = None
        if str(self.device).startswith("cuda") and self.torch.cuda.is_available():
            cuda_rng = self.torch.cuda.get_rng_state_all()
        self.torch.save({
            "schema_version": 1,
            "step": step,
            "model": self.model.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "buffer": self.buffer,
            "python_rng_state": self.rng.getstate(),
            "torch_rng_state": self.torch.get_rng_state(),
            "cuda_rng_state_all": cuda_rng,
        }, temporary)
        temporary.replace(path)

    def load_recovery(self, path: Path, *, expected_step: int) -> None:
        try:
            payload = self.torch.load(
                path, map_location=self.device, weights_only=False
            )
        except TypeError:  # torch < 2.6 has no weights_only keyword.
            payload = self.torch.load(path, map_location=self.device)
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("step") != expected_step
        ):
            raise MemSkillError("MemSkill PPO recovery checkpoint is invalid")
        self.model.load_state_dict(payload["model"])
        self.optimizer.load_state_dict(payload["optimizer"])
        self.buffer = list(payload.get("buffer", []))
        self.rng.setstate(payload["python_rng_state"])
        self.torch.set_rng_state(payload["torch_rng_state"].cpu())
        cuda_rng = payload.get("cuda_rng_state_all")
        if (
            cuda_rng is not None
            and str(self.device).startswith("cuda")
            and self.torch.cuda.is_available()
        ):
            # torch.load(map_location="cuda") also moves the serialized CUDA
            # RNG byte tensors onto the GPU.  PyTorch's RNG setter requires
            # CPU ByteTensors even though the states belong to CUDA devices.
            self.torch.cuda.set_rng_state_all([
                state.cpu() for state in cuda_rng
            ])

    def save_random_state(self, path: Path) -> None:
        """Save RNG state immediately after a stochastic action selection."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        cuda_rng = None
        if str(self.device).startswith("cuda") and self.torch.cuda.is_available():
            cuda_rng = self.torch.cuda.get_rng_state_all()
        self.torch.save({
            "schema_version": 1,
            "torch_rng_state": self.torch.get_rng_state(),
            "cuda_rng_state_all": cuda_rng,
        }, temporary)
        temporary.replace(path)

    def load_random_state(self, path: Path) -> None:
        try:
            payload = self.torch.load(
                path, map_location=self.device, weights_only=False
            )
        except TypeError:
            payload = self.torch.load(path, map_location=self.device)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise MemSkillError("MemSkill selection RNG checkpoint is invalid")
        self.torch.set_rng_state(payload["torch_rng_state"].cpu())
        cuda_rng = payload.get("cuda_rng_state_all")
        if (
            cuda_rng is not None
            and str(self.device).startswith("cuda")
            and self.torch.cuda.is_available()
        ):
            self.torch.cuda.set_rng_state_all([
                state.cpu() for state in cuda_rng
            ])


class MemSkillTrainer:
    method = "memskill"
    memory_top_k = 20
    action_top_k = 3
    controller_batch_size = 4
    ppo_epochs = 2
    failure_window = 100
    failure_pool_size = 2000
    failure_clusters = 5
    samples_per_cluster = 3
    designer_reflection_cycles = 3
    designer_max_changes = 3
    max_designer_evolves = 6
    designer_patience = 3
    maximum_operations = 30
    random_seed = 20260826

    def __init__(
        self,
        *,
        protocol: EvaluationProtocol,
        embedder: Embedder,
        provider: LearningProvider,
        device: str = "cuda",
    ):
        self.protocol = protocol
        self.embedder = embedder
        self.provider = provider
        self.device = device

    @staticmethod
    def _session_text(episode: TrajectoryEpisode) -> str:
        compact = json.dumps(
            episode.compact_trace(maximum_candidate_chars=3500),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (episode.state_text() + "\nTRAJECTORY:\n" + compact)[:18000]

    @staticmethod
    def _executor_messages(
        episode: TrajectoryEpisode,
        session: str,
        retrieved: Sequence[MemSkillItem],
        selected: Sequence[MemoryOperation],
    ) -> list[dict[str, str]]:
        allowed = [item.name for item in selected]
        return [{
            "role": "system",
            "content": (
                "You are MemSkill's memory-operation executor for PLC code "
                "generation trajectories. Apply only selected operations. "
                "Store reusable, evidence-grounded procedural knowledge, including "
                "verified success patterns and explicit failure warnings. Never "
                "store credentials or hidden Oracle payloads. Return JSON exactly "
                "as {\"actions\":[{\"operation\":string,\"memory_id\":string|null,"
                "\"content\":string|null,\"rationale\":string},...]}. "
                "Return at most one action per selected operation. Keep each content "
                "within 1000 characters and each rationale within 200 characters. "
                "Summarize the reusable procedure; do not copy complete PLC programs "
                "or entire retrieved memories. Keep all required fields and close the JSON object."
            ),
        }, {
            "role": "user",
            "content": json.dumps({
                "scope": [episode.target, episode.output_language],
                "selected_operations": [{
                    "name": item.name,
                    "update_type": item.update_type,
                    "instruction": item.instruction,
                } for item in selected],
                "allowed_operation_names": allowed,
                "retrieved_memories": [{
                    "memory_id": item.memory_id,
                    "content": item.content,
                } for item in retrieved],
                "trajectory": session,
            }, ensure_ascii=False, sort_keys=True),
        }]

    @staticmethod
    def _parse_actions(
        document: dict[str, Any],
        *,
        selected: Sequence[MemoryOperation],
        retrieved: Sequence[MemSkillItem],
    ) -> list[dict[str, Any]]:
        actions = document.get("actions")
        if not isinstance(actions, list) or not actions:
            # Match the upstream executor: an unparsable/empty response is an
            # unsuccessful NOOP, not a fatal training-process error.
            return []
        allowed = {item.name: item for item in selected}
        retrieved_ids = {item.memory_id for item in retrieved}
        result: list[dict[str, Any]] = []
        for raw in actions:
            if not isinstance(raw, dict) or raw.get("operation") not in allowed:
                continue
            operation = allowed[str(raw["operation"])]
            memory_id = raw.get("memory_id")
            content = raw.get("content")
            if operation.update_type in {"update", "delete"} and memory_id not in retrieved_ids:
                # The upstream MemSkill executor treats an out-of-range memory
                # target as an unsuccessful NOOP and continues training.  Drop
                # the invalid action here as the ID-based equivalent.  The
                # selected operation consequently receives no process reward.
                continue
            if operation.update_type in {"insert", "update"} and (
                not isinstance(content, str) or not content.strip()
            ):
                continue
            if isinstance(content, str) and len(content) > 5000:
                continue
            result.append({
                "operation": operation.name,
                "update_type": operation.update_type,
                "memory_id": str(memory_id) if memory_id is not None else None,
                "content": content.strip() if isinstance(content, str) else None,
                "rationale": str(raw.get("rationale", ""))[:1000],
            })
        return result

    @staticmethod
    def _apply_actions(
        actions: Sequence[dict[str, Any]],
        *,
        episode: TrajectoryEpisode,
        memories: list[MemSkillItem],
        step: int,
    ) -> set[str]:
        applied: set[str] = set()
        for number, action in enumerate(actions, 1):
            operation = str(action["operation"])
            update_type = str(action["update_type"])
            if update_type == "noop":
                applied.add(operation)
                continue
            if update_type == "insert":
                identifier = "memory_" + digest({
                    "source_task_id": episode.task_id,
                    "step": step,
                    "number": number,
                    "content": action["content"],
                })[:24]
                memories.append(MemSkillItem(
                    memory_id=identifier,
                    content=str(action["content"]),
                    target=episode.target,
                    output_language=episode.output_language,
                    category_id=episode.category_id,
                    semantic_signature=episode.semantic_signature,
                    source_task_id=episode.task_id,
                    source_reward=episode.reward,
                    operation_history=[operation],
                    content_history=[],
                    created_step=step,
                    last_updated_step=step,
                ))
                applied.add(operation)
                continue
            positions = [
                index for index, item in enumerate(memories)
                if item.memory_id == action["memory_id"]
            ]
            if len(positions) != 1:
                raise MemSkillError("memory action target disappeared")
            index = positions[0]
            if update_type == "delete":
                memories.pop(index)
            else:
                item = memories[index]
                item.content_history.append(item.content)
                item.content = str(action["content"])
                item.operation_history.append(operation)
                item.last_updated_step = step
                item.source_task_id = episode.task_id
                item.source_reward = episode.reward
                item.category_id = episode.category_id
                item.semantic_signature = episode.semantic_signature
            applied.add(operation)
        return applied

    @staticmethod
    def _validation_score(
        validation: Sequence[TrajectoryEpisode],
        memories: Sequence[MemSkillItem],
        memory_vectors: Sequence[Sequence[float]],
        validation_vectors: Sequence[Sequence[float]],
    ) -> float:
        if not memories:
            return 0.0
        scores = []
        for episode, vector in zip(validation, validation_vectors):
            matches = _top_matches(
                vector, memories, memory_vectors,
                scope=episode.scope, top_k=20,
            )
            if not matches:
                scores.append(0.0)
                continue
            gains = []
            for rank, (index, _) in enumerate(matches, 1):
                memory = memories[index]
                relevant = (
                    memory.category_id == episode.category_id
                    and (memory.source_reward >= 0.5) == episode.success
                )
                gains.append((1.0 if relevant else 0.0) / math.log2(rank + 1))
            ideal = sum(
                1.0 / math.log2(rank + 1)
                for rank in range(1, min(20, len(matches)) + 1)
            )
            scores.append(sum(gains) / ideal if ideal else 0.0)
        return sum(scores) / len(scores)

    @staticmethod
    def _cluster_failures(
        failures: Sequence[tuple[TrajectoryEpisode, Sequence[float]]],
        *,
        cluster_count: int,
    ) -> list[list[TrajectoryEpisode]]:
        """Deterministic cosine k-means for the Designer failure pool."""
        if not failures:
            return []
        ordered = sorted(failures, key=lambda item: item[0].task_id)
        count = min(cluster_count, len(ordered))
        centroids = [list(ordered[index][1]) for index in range(count)]
        assignments = [0] * len(ordered)
        for _ in range(20):
            next_assignments = [
                max(
                    range(count),
                    key=lambda index: (cosine(vector, centroids[index]), -index),
                )
                for _, vector in ordered
            ]
            if next_assignments == assignments:
                break
            assignments = next_assignments
            for cluster in range(count):
                vectors = [
                    ordered[index][1]
                    for index, assigned in enumerate(assignments)
                    if assigned == cluster
                ]
                if vectors:
                    centroids[cluster] = normalize([
                        sum(vector[dimension] for vector in vectors) / len(vectors)
                        for dimension in range(len(vectors[0]))
                    ])
        result: list[list[TrajectoryEpisode]] = [[] for _ in range(count)]
        for (episode, _), assigned in zip(ordered, assignments):
            result[assigned].append(episode)
        return [items for items in result if items]

    @staticmethod
    def _designer_messages(
        failures: Sequence[TrajectoryEpisode],
        operations: Sequence[MemoryOperation],
        feedback: Sequence[dict[str, Any]],
    ) -> list[dict[str, str]]:
        return [{
            "role": "system",
            "content": (
                "You are MemSkill's operation-bank Designer. Analyze clustered "
                "PLC memory-management failures and propose at most three "
                "operation changes. Changes may add_new, refine_existing, or "
                "delete_existing. Each operation must remain a memory management "
                "procedure, not a task answer. Return JSON as {\"analysis\":string,"
                "\"changes\":[{\"action\":string,\"target\":string|null,"
                "\"name\":string,\"description\":string,\"instruction\":string,"
                "\"update_type\":\"insert|update|delete|noop\"}]}."
            ),
        }, {
            "role": "user",
            "content": json.dumps({
                "operation_bank": [item.to_dict() for item in operations],
                "cluster_samples": [
                    episode.compact_trace(maximum_candidate_chars=1200)
                    for episode in failures
                ],
                "evolution_feedback": list(feedback)[-6:],
            }, ensure_ascii=False, sort_keys=True),
        }]

    @staticmethod
    def _reflection_messages(
        *,
        prior: dict[str, Any],
        cycle: int,
        operations: Sequence[MemoryOperation],
    ) -> list[dict[str, str]]:
        return [{
            "role": "system",
            "content": (
                "You are performing a MemSkill Designer reflection cycle. "
                "Critically check whether the proposed operation changes are "
                "grounded in recurring failures, non-duplicative, safe for PLC "
                "memory management, and compatible with the existing operation "
                "bank. Revise or remove weak changes. Keep at most three changes "
                "and return the same JSON schema: {\"analysis\":string,"
                "\"changes\":[{\"action\":string,\"target\":string|null,"
                "\"name\":string,\"description\":string,\"instruction\":string,"
                "\"update_type\":\"insert|update|delete|noop\"}]}."
            ),
        }, {
            "role": "user",
            "content": json.dumps({
                "reflection_cycle": cycle,
                "existing_operation_names": [item.name for item in operations],
                "prior_proposal": prior,
            }, ensure_ascii=False, sort_keys=True),
        }]

    @staticmethod
    def _apply_designer_changes(
        operations: dict[str, MemoryOperation],
        document: dict[str, Any],
        *,
        evolution: int,
    ) -> set[str]:
        # The upstream Designer treats malformed proposals as ``no_change`` and
        # applies valid changes independently.  A stochastic formatting error
        # must not terminate a long, already-paid formation run.
        changes = document.get("changes") if isinstance(document, dict) else None
        if not isinstance(changes, list):
            return set()
        changed: set[str] = set()
        action_aliases = {
            "add_new": "add_new",
            "insert": "add_new",
            "refine_existing": "refine_existing",
            "update": "refine_existing",
            "delete_existing": "delete_existing",
            "delete": "delete_existing",
        }
        protected = {"insert", "update", "delete", "noop"}
        for raw in changes[:3]:
            if not isinstance(raw, dict):
                continue
            action = action_aliases.get(
                str(raw.get("action", "")).casefold().strip()
            )
            if action is None:
                continue
            target = raw.get("target")
            target = target.strip() if isinstance(target, str) else ""
            if action == "delete_existing":
                if target not in operations or target in protected:
                    continue
                candidate = copy.deepcopy(operations)
                del candidate[target]
                operations.clear()
                operations.update(candidate)
                changed.discard(target)
                continue
            name = str(raw.get("name", "")).strip()
            description = str(raw.get("description", "")).strip()
            instruction = str(raw.get("instruction", "")).strip()
            update_type = str(raw.get("update_type", "")).casefold().strip()
            if (
                not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name)
                or not description or not instruction
                or update_type not in {"insert", "update", "delete", "noop"}
            ):
                continue
            operation = MemoryOperation(
                name=name,
                description=description,
                instruction=instruction,
                update_type=update_type,
                created_at=f"evolution_{evolution}",
            )
            candidate = copy.deepcopy(operations)
            if action == "refine_existing":
                if (
                    target not in candidate
                    or (name != target and name in candidate)
                ):
                    continue
                old = candidate.pop(target)
                operation.usage_count = old.usage_count
                operation.average_reward = old.average_reward
            elif name in candidate:
                continue
            elif len(candidate) >= 30:
                eligible = [
                    item for item in candidate.values()
                    if item.name not in protected
                ]
                if not eligible:
                    continue
                worst = min(eligible, key=lambda item: (item.average_reward, item.usage_count, item.name))
                del candidate[worst.name]
                changed.discard(worst.name)
            candidate[name] = operation
            operations.clear()
            operations.update(candidate)
            if action == "refine_existing":
                changed.discard(target)
            changed.add(name)
        return changed

    def _journaled_json_chat(
        self,
        workspace: Path,
        *,
        phase: str,
        step: int,
        messages: Sequence[dict[str, str]],
        max_tokens: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Reuse a completed API response if a process died before state commit."""
        request_sha256 = digest({
            "messages": list(messages),
            "max_tokens": max_tokens,
        })
        path = workspace / f"call_{phase}.json"
        if path.exists():
            try:
                journal = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MemSkillError(f"invalid MemSkill call journal: {path}") from exc
            if (
                not isinstance(journal, dict)
                or journal.get("schema_version") != 1
                or journal.get("phase") != phase
                or journal.get("step") != step
                or journal.get("request_sha256") != request_sha256
                or not isinstance(journal.get("document"), dict)
                or not isinstance(journal.get("audit"), dict)
            ):
                raise MemSkillError("MemSkill call journal does not match the resumed step")
            return dict(journal["document"]), dict(journal["audit"])
        document, call = self.provider.json_chat(messages, max_tokens=max_tokens)
        audit = _call_audit(call, phase=phase, step=step)
        write_json(path, {
            "schema_version": 1,
            "phase": phase,
            "step": step,
            "request_sha256": request_sha256,
            "document": document,
            "audit": audit,
        })
        return document, audit

    @staticmethod
    def _load_committed_selection(
        root: Path,
        *,
        controller: TorchPPOController,
        step: int,
        task_id: str,
        state_sha256: str,
        operations_sha256: str,
        selection_inputs_sha256: str,
        operation_names: Sequence[str],
    ) -> tuple[list[int], float, float] | None:
        selection_root = root / "selection"
        if not selection_root.exists():
            return None
        try:
            selection = json.loads(
                (selection_root / "selection.json").read_text(encoding="utf-8")
            )
            complete = json.loads(
                (selection_root / "complete.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise MemSkillError("MemSkill in-flight selection is incomplete") from exc
        rng_path = selection_root / "rng_after_selection.pt"
        if (
            not isinstance(selection, dict)
            or selection.get("schema_version") not in {1, 2}
            or selection.get("step") != step
            or selection.get("task_id") != task_id
            or selection.get("operation_names") != list(operation_names)
            or not isinstance(complete, dict)
            or complete.get("selection_sha256")
            != file_sha256(selection_root / "selection.json")
            or complete.get("rng_sha256") != file_sha256(rng_path)
        ):
            raise MemSkillError("MemSkill in-flight selection changed or is corrupt")
        if selection.get("schema_version") == 2:
            if selection.get("selection_inputs_sha256") != selection_inputs_sha256:
                raise MemSkillError(
                    "MemSkill in-flight stable selection inputs changed"
                )
        elif not (root / "call_memory_executor.json").is_file():
            # Schema 1 bound the selection to hashes of GPU-produced float
            # vectors.  Re-encoding the same inputs may change their last bits,
            # so exact vector hashes are not a portable restart boundary.  A
            # legacy selection is reusable only when its paid executor call is
            # already journaled; that journal subsequently verifies the full
            # prompt, retrieved memories, and selected operations.
            raise MemSkillError(
                "legacy MemSkill selection lacks a completed executor journal"
            )
        if (
            not isinstance(selection.get("state_sha256"), str)
            or not isinstance(selection.get("operations_sha256"), str)
        ):
            raise MemSkillError("MemSkill selection omitted vector diagnostics")
        indices = selection.get("selected_indices")
        if (
            not isinstance(indices, list)
            or not indices
            or len(set(indices)) != len(indices)
            or any(not isinstance(item, int) or item < 0 or item >= len(operation_names) for item in indices)
        ):
            raise MemSkillError("MemSkill in-flight action indices are invalid")
        controller.load_random_state(rng_path)
        return (
            [int(item) for item in indices],
            float(selection["log_probability"]),
            float(selection["value"]),
        )

    @staticmethod
    def _commit_selection(
        root: Path,
        *,
        controller: TorchPPOController,
        step: int,
        task_id: str,
        state_sha256: str,
        operations_sha256: str,
        selection_inputs_sha256: str,
        operation_names: Sequence[str],
        selected_indices: Sequence[int],
        log_probability: float,
        value: float,
    ) -> None:
        temporary = root / "selection.tmp"
        final = root / "selection"
        if final.exists():
            raise MemSkillError("MemSkill selection was committed twice")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        selection_path = temporary / "selection.json"
        rng_path = temporary / "rng_after_selection.pt"
        write_json(selection_path, {
            "schema_version": 2,
            "step": step,
            "task_id": task_id,
            "state_sha256": state_sha256,
            "operations_sha256": operations_sha256,
            "selection_inputs_sha256": selection_inputs_sha256,
            "operation_names": list(operation_names),
            "selected_indices": [int(item) for item in selected_indices],
            "log_probability": log_probability,
            "value": value,
        })
        controller.save_random_state(rng_path)
        write_json(temporary / "complete.json", {
            "schema_version": 1,
            "selection_sha256": file_sha256(selection_path),
            "rng_sha256": file_sha256(rng_path),
        })
        temporary.replace(final)

    @staticmethod
    def _save_recovery_checkpoint(
        output_root: Path,
        *,
        state: dict[str, Any],
        controller: TorchPPOController,
    ) -> None:
        step = int(state["step"])
        root = output_root / "recovery_checkpoints"
        root.mkdir(parents=True, exist_ok=True)
        temporary = root / f"step_{step:04d}.tmp"
        final = root / f"step_{step:04d}"
        if final.exists():
            raise MemSkillError("MemSkill recovery step was committed twice")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        state_path = temporary / "state.json"
        controller_path = temporary / "controller.pt"
        write_json(state_path, state)
        controller.save_recovery(controller_path, step=step)
        write_json(temporary / "complete.json", {
            "schema_version": 1,
            "step": step,
            "state_sha256": file_sha256(state_path),
            "controller_sha256": file_sha256(controller_path),
        })
        temporary.replace(final)
        completed = sorted(
            path for path in root.iterdir()
            if path.is_dir() and re.fullmatch(r"step_[0-9]{4}", path.name)
        )
        for obsolete in completed[:-2]:
            shutil.rmtree(obsolete)

    @staticmethod
    def _load_recovery_checkpoint(
        output_root: Path,
        *,
        controller: TorchPPOController,
        training_sequence_sha256: str,
        protocol_sha256: str,
        corpus_binding_sha256: str,
        source_revision: str,
    ) -> dict[str, Any] | None:
        root = output_root / "recovery_checkpoints"
        if not root.exists():
            return None
        completed = sorted(
            (
                path for path in root.iterdir()
                if path.is_dir() and re.fullmatch(r"step_[0-9]{4}", path.name)
            ),
            reverse=True,
        )
        if not completed:
            return None
        latest = completed[0]
        try:
            state = json.loads((latest / "state.json").read_text(encoding="utf-8"))
            complete = json.loads((latest / "complete.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MemSkillError("MemSkill recovery checkpoint is unreadable") from exc
        controller_path = latest / "controller.pt"
        state_path = latest / "state.json"
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or not isinstance(complete, dict)
            or complete.get("schema_version") != 1
            or complete.get("step") != state.get("step")
            or complete.get("state_sha256") != file_sha256(state_path)
            or complete.get("controller_sha256") != file_sha256(controller_path)
            or state.get("training_sequence_sha256") != training_sequence_sha256
            or state.get("protocol_sha256") != protocol_sha256
            or state.get("corpus_binding_sha256") != corpus_binding_sha256
            or state.get("source_revision") != source_revision
        ):
            raise MemSkillError("MemSkill recovery checkpoint binding changed or is corrupt")
        controller.load_recovery(controller_path, expected_step=int(state["step"]))
        return state

    def train_and_freeze(
        self,
        episodes: Iterable[TrajectoryEpisode],
        *,
        output_root: Path,
        corpus_binding: dict[str, Any],
        source_revision: str,
        expected_count: int = 1000,
    ) -> dict[str, Any]:
        if (output_root / "freeze_manifest.json").exists():
            return verify_manifest(
                output_root,
                method=self.method,
                protocol_sha256=self.protocol.sha256,
            )
        output_root.mkdir(parents=True, exist_ok=True)
        ordered = tuple(sorted(episodes, key=lambda item: item.task_id))
        if len(ordered) != expected_count or len({item.task_id for item in ordered}) != expected_count:
            raise MemSkillError(f"MemSkill requires {expected_count} unique trajectories")
        training, validation = deterministic_development_partition(ordered)
        training_sequence_sha256 = digest([item.task_id for item in training])
        corpus_binding_sha256 = digest(corpus_binding)
        session_texts = [self._session_text(item) for item in training]
        validation_texts = [item.state_text() for item in validation]
        session_vectors = [normalize(item) for item in self.embedder.encode(session_texts)]
        validation_vectors = [normalize(item) for item in self.embedder.encode(validation_texts)]
        if not session_vectors or len(session_vectors) != len(training):
            raise MemSkillError("state encoder returned an incomplete training batch")
        dimension = len(session_vectors[0])
        controller = TorchPPOController(
            state_dim=dimension * 2,
            operation_dim=dimension,
            device=self.device,
            seed=self.random_seed,
            action_top_k=self.action_top_k,
        )
        recovered = self._load_recovery_checkpoint(
            output_root,
            controller=controller,
            training_sequence_sha256=training_sequence_sha256,
            protocol_sha256=self.protocol.sha256,
            corpus_binding_sha256=corpus_binding_sha256,
            source_revision=source_revision,
        )
        if recovered is None:
            completed_step = 0
            operations = _initial_operations()
            memories: list[MemSkillItem] = []
            failures: list[tuple[TrajectoryEpisode, Sequence[float]]] = []
            audits: list[dict[str, Any]] = []
            training_log: list[dict[str, Any]] = []
            evolution_feedback: list[dict[str, Any]] = []
            best_operations = copy.deepcopy(operations)
            best_score = -1.0
            evolves = 0
            no_improvement = 0
            new_operations: set[str] = set()
            new_bias_steps_remaining = 0
            stop_training = False
        else:
            completed_step = int(recovered.get("step", -1))
            if (
                completed_step < 1
                or completed_step > len(training)
                or recovered.get("task_id") != training[completed_step - 1].task_id
            ):
                raise MemSkillError("MemSkill recovery step does not match the corpus")
            operations = {
                item.name: item
                for item in (
                    MemoryOperation.from_dict(value)
                    for value in recovered.get("operations", [])
                )
            }
            memories = [
                MemSkillItem.from_dict(value)
                for value in recovered.get("memories", [])
            ]
            best_operations = {
                item.name: item
                for item in (
                    MemoryOperation.from_dict(value)
                    for value in recovered.get("best_operations", [])
                )
            }
            training_positions = {
                item.task_id: index for index, item in enumerate(training)
            }
            try:
                failures = [
                    (
                        training[training_positions[str(task_id)]],
                        session_vectors[training_positions[str(task_id)]],
                    )
                    for task_id in recovered.get("failure_task_ids", [])
                ]
            except KeyError as exc:
                raise MemSkillError("MemSkill recovery references an unknown failure") from exc
            audits = [dict(value) for value in recovered.get("audits", [])]
            training_log = [dict(value) for value in recovered.get("training_log", [])]
            evolution_feedback = [
                dict(value) for value in recovered.get("evolution_feedback", [])
            ]
            best_score = float(recovered.get("best_score", -1.0))
            evolves = int(recovered.get("evolves", 0))
            no_improvement = int(recovered.get("no_improvement", 0))
            new_operations = {
                str(value) for value in recovered.get("new_operations", [])
            }
            new_bias_steps_remaining = int(
                recovered.get("new_bias_steps_remaining", 0)
            )
            stop_training = bool(recovered.get("stop_training", False))
            if (
                not operations
                or not best_operations
                or len(training_log) != completed_step
            ):
                raise MemSkillError("MemSkill recovery state is incomplete")

        memory_vector_cache: dict[str, tuple[str, list[float]]] = {}
        if memories:
            restored_vectors = self.embedder.encode([item.content for item in memories])
            if len(restored_vectors) != len(memories):
                raise MemSkillError("memory encoder could not restore the checkpoint")
            for item, vector in zip(memories, restored_vectors):
                memory_vector_cache[item.memory_id] = (
                    digest(item.content), normalize(vector)
                )
        memory_vectors = [
            memory_vector_cache[item.memory_id][1] for item in memories
        ]

        inflight_root = output_root / "inflight"
        inflight_root.mkdir(parents=True, exist_ok=True)
        for path in tuple(inflight_root.iterdir()):
            match = re.fullmatch(r"step_([0-9]{4})", path.name)
            if path.is_dir() and match and int(match.group(1)) <= completed_step:
                shutil.rmtree(path)

        for step in range(completed_step + 1, len(training) + 1):
            if stop_training:
                break
            episode = training[step - 1]
            session = session_texts[step - 1]
            session_vector = session_vectors[step - 1]
            workspace = inflight_root / f"step_{step:04d}"
            workspace.mkdir(parents=True, exist_ok=True)
            identity_path = workspace / "identity.json"
            identity = {
                "schema_version": 1,
                "step": step,
                "task_id": episode.task_id,
                "training_sequence_sha256": training_sequence_sha256,
            }
            if identity_path.exists():
                try:
                    existing_identity = json.loads(
                        identity_path.read_text(encoding="utf-8")
                    )
                except json.JSONDecodeError as exc:
                    raise MemSkillError("MemSkill in-flight identity is corrupt") from exc
                if existing_identity != identity:
                    raise MemSkillError("MemSkill in-flight task sequence changed")
            else:
                write_json(identity_path, identity)
            matches = _top_matches(
                session_vector, memories, memory_vectors,
                scope=episode.scope, top_k=self.memory_top_k,
            ) if memories else []
            retrieved = [memories[index] for index, _ in matches]
            retrieved_vectors = [memory_vectors[index] for index, _ in matches]
            mean_memory = (
                [
                    sum(vector[index] for vector in retrieved_vectors) / len(retrieved_vectors)
                    for index in range(dimension)
                ]
                if retrieved_vectors else [0.0] * dimension
            )
            state = [*session_vector, *mean_memory]
            operation_names = sorted(operations)
            operation_vectors = [
                normalize(vector) for vector in self.embedder.encode([
                    operations[name].description for name in operation_names
                ])
            ]
            mask = [1.0 if name in new_operations else 0.0 for name in operation_names]
            state_sha256 = digest(state)
            operations_sha256 = digest(operation_vectors)
            selection_inputs_sha256 = digest({
                "step": step,
                "task_id": episode.task_id,
                "session_sha256": digest(session),
                "retrieved_memories": [{
                    "memory_id": item.memory_id,
                    "content_sha256": digest(item.content),
                } for item in retrieved],
                "operations": [
                    operations[name].to_dict() for name in operation_names
                ],
            })
            selection = self._load_committed_selection(
                workspace,
                controller=controller,
                step=step,
                task_id=episode.task_id,
                state_sha256=state_sha256,
                operations_sha256=operations_sha256,
                selection_inputs_sha256=selection_inputs_sha256,
                operation_names=operation_names,
            )
            if selection is None:
                selected_indices, log_probability, value = controller.select(
                    state,
                    operation_vectors,
                    deterministic=False,
                    new_operation_mask=mask,
                    new_action_bias_scale=(
                        new_bias_steps_remaining / 25
                        if new_bias_steps_remaining else 0.0
                    ),
                )
                self._commit_selection(
                    workspace,
                    controller=controller,
                    step=step,
                    task_id=episode.task_id,
                    state_sha256=state_sha256,
                    operations_sha256=operations_sha256,
                    selection_inputs_sha256=selection_inputs_sha256,
                    operation_names=operation_names,
                    selected_indices=selected_indices,
                    log_probability=log_probability,
                    value=value,
                )
            else:
                selected_indices, log_probability, value = selection
            selected = [operations[operation_names[index]] for index in selected_indices]
            document, executor_audit = self._journaled_json_chat(
                workspace,
                phase="memory_executor",
                step=step,
                messages=self._executor_messages(
                    episode, session, retrieved, selected
                ),
                max_tokens=2600,
            )
            audits.append(executor_audit)
            actions = self._parse_actions(document, selected=selected, retrieved=retrieved)
            applied = self._apply_actions(
                actions, episode=episode, memories=memories, step=step
            )
            # Encode only inserted/updated memories. Re-encoding the full growing
            # bank at every step is quadratic and made the original collector
            # needlessly slow on a 1000-trajectory corpus.
            current_ids = {item.memory_id for item in memories}
            memory_vector_cache = {
                key: value for key, value in memory_vector_cache.items()
                if key in current_ids
            }
            changed_items = [
                item for item in memories
                if item.memory_id not in memory_vector_cache
                or memory_vector_cache[item.memory_id][0] != digest(item.content)
            ]
            if changed_items:
                changed_vectors = self.embedder.encode([
                    item.content for item in changed_items
                ])
                if len(changed_vectors) != len(changed_items):
                    raise MemSkillError("memory encoder returned an incomplete batch")
                for item, vector in zip(changed_items, changed_vectors):
                    memory_vector_cache[item.memory_id] = (
                        digest(item.content), normalize(vector)
                    )
            memory_vectors = [
                memory_vector_cache[item.memory_id][1] for item in memories
            ]
            process_reward = 0.10 * (len(applied) / max(1, len(selected)))
            reward = min(1.10, episode.reward + process_reward)
            for operation in selected:
                operation.update_reward(reward if operation.name in applied else 0.0)
            controller.add_transition(
                state=state,
                operation_vectors=operation_vectors,
                actions=selected_indices,
                log_probability=log_probability,
                value=value,
                reward=reward,
            )
            ppo = {}
            if step % self.controller_batch_size == 0:
                ppo = controller.update(epochs=self.ppo_epochs)
            if episode.reward < 0.5:
                failures.append((episode, session_vector))
                failures = failures[-self.failure_pool_size:]
            training_log.append({
                "step": step,
                "task_id": episode.task_id,
                "selected_operations": [item.name for item in selected],
                "applied_operations": sorted(applied),
                "terminal_reward": episode.reward,
                "process_reward": process_reward,
                "combined_reward": reward,
                "memory_count": len(memories),
                **ppo,
            })
            if new_bias_steps_remaining:
                new_bias_steps_remaining -= 1
                if new_bias_steps_remaining == 0:
                    new_operations = set()

            if step % self.failure_window == 0 and evolves < self.max_designer_evolves:
                score = self._validation_score(
                    validation, memories, memory_vectors, validation_vectors
                )
                if score > best_score:
                    best_score = score
                    best_operations = copy.deepcopy(operations)
                    no_improvement = 0
                    snapshot_outcome = "new_best"
                else:
                    operations = copy.deepcopy(best_operations)
                    no_improvement += 1
                    snapshot_outcome = "rollback_to_best"
                clusters = self._cluster_failures(
                    failures[-self.failure_window:], cluster_count=self.failure_clusters
                )
                samples = [
                    item
                    for cluster in clusters
                    for item in sorted(
                        cluster, key=lambda episode: episode.task_id
                    )[:self.samples_per_cluster]
                ]
                if not samples or no_improvement >= self.designer_patience:
                    evolution_feedback.append({
                        "evolution": evolves,
                        "step": step,
                        "validation_score": score,
                        "outcome": snapshot_outcome,
                        "stopped": no_improvement >= self.designer_patience,
                    })
                    stop_training = no_improvement >= self.designer_patience
                else:
                    document, designer_audit = self._journaled_json_chat(
                        workspace,
                        phase="operation_designer",
                        step=step,
                        messages=self._designer_messages(
                            samples, list(operations.values()), evolution_feedback
                        ),
                        max_tokens=3600,
                    )
                    audits.append(designer_audit)
                    for cycle in range(2, self.designer_reflection_cycles + 1):
                        phase = f"operation_designer_reflection_{cycle}"
                        document, reflection_audit = self._journaled_json_chat(
                            workspace,
                            phase=phase,
                            step=step,
                            messages=self._reflection_messages(
                                prior=document,
                                cycle=cycle,
                                operations=list(operations.values()),
                            ),
                            max_tokens=3600,
                        )
                        audits.append(reflection_audit)
                    evolves += 1
                    changed = self._apply_designer_changes(
                        operations, document, evolution=evolves
                    )
                    new_operations = changed
                    new_bias_steps_remaining = 25 if changed else 0
                    evolution_feedback.append({
                        "evolution": evolves,
                        "step": step,
                        "validation_score": score,
                        "outcome": snapshot_outcome,
                        "changed_operations": sorted(changed),
                        "analysis_sha256": digest(str(document.get("analysis", ""))),
                    })

            recovery_state = {
                "schema_version": 1,
                "step": step,
                "task_id": episode.task_id,
                "training_sequence_sha256": training_sequence_sha256,
                "protocol_sha256": self.protocol.sha256,
                "corpus_binding_sha256": corpus_binding_sha256,
                "source_revision": source_revision,
                "operations": [
                    item.to_dict()
                    for item in sorted(operations.values(), key=lambda item: item.name)
                ],
                "memories": [item.to_dict() for item in memories],
                "failure_task_ids": [item.task_id for item, _ in failures],
                "audits": audits,
                "training_log": training_log,
                "evolution_feedback": evolution_feedback,
                "best_operations": [
                    item.to_dict()
                    for item in sorted(
                        best_operations.values(), key=lambda item: item.name
                    )
                ],
                "best_score": best_score,
                "evolves": evolves,
                "no_improvement": no_improvement,
                "new_operations": sorted(new_operations),
                "new_bias_steps_remaining": new_bias_steps_remaining,
                "stop_training": stop_training,
            }
            self._save_recovery_checkpoint(
                output_root, state=recovery_state, controller=controller
            )
            shutil.rmtree(workspace)
            if stop_training:
                break

        if controller.buffer:
            controller.update(epochs=self.ppo_epochs)
        if not memories:
            raise MemSkillError("MemSkill learned no memories")
        final_score = self._validation_score(
            validation, memories, memory_vectors, validation_vectors
        )
        if final_score >= best_score:
            best_score = final_score
            best_operations = copy.deepcopy(operations)
        write_jsonl(output_root / "memories.jsonl", (item.to_dict() for item in memories))
        write_json(output_root / "operation_bank.json", {
            "schema_version": 1,
            "operations": [
                item.to_dict() for item in sorted(best_operations.values(), key=lambda item: item.name)
            ],
        })
        write_jsonl(output_root / "training_log.jsonl", training_log)
        write_jsonl(output_root / "evolution_log.jsonl", evolution_feedback)
        write_jsonl(output_root / "model_calls.jsonl", audits)
        controller.save(output_root / "ppo_controller.pt")
        index = DenseIndex.build(
            item_ids=[item.memory_id for item in memories],
            texts=[item.content for item in memories],
            metadata=[{
                "target": item.target,
                "output_language": item.output_language,
                "category_id": item.category_id,
                "semantic_signature": item.semantic_signature,
                "source_task_id": item.source_task_id,
                "source_reward": item.source_reward,
            } for item in memories],
            embedder=self.embedder,
        )
        index.save(output_root / "memory_index")
        return seal_manifest(output_root, {
            "schema_version": 1,
            "method": self.method,
            "method_variant": "ppo_dynamic_operation_bank_plc_adapter",
            "memory_top_k": self.memory_top_k,
            "action_top_k": self.action_top_k,
            "controller_hidden_dimension": 256,
            "controller_learning_rate": 1e-4,
            "ppo_clip_epsilon": 0.2,
            "ppo_epochs": self.ppo_epochs,
            "failure_window": self.failure_window,
            "failure_pool_size": self.failure_pool_size,
            "failure_clusters": self.failure_clusters,
            "samples_per_cluster": self.samples_per_cluster,
            "designer_reflection_cycles": self.designer_reflection_cycles,
            "designer_max_changes": self.designer_max_changes,
            "max_designer_evolves": self.max_designer_evolves,
            "designer_patience": self.designer_patience,
            "encoder": {
                "name": self.embedder.model_name,
                "revision": self.embedder.revision,
                "frozen": True,
            },
            "memory_count": len(memories),
            "operation_count": len(best_operations),
            **summarize_model_audits(audits),
            "completed_training_steps": len(training_log),
            "early_stopped": stop_training,
            "recovery_checkpointing": "atomic_per_completed_training_step",
            "validation_proxy": "scope_filtered_retrieval_ndcg20",
            "best_validation_score": best_score,
            "random_seed": self.random_seed,
            "corpus_binding": corpus_binding,
            "protocol_sha256": self.protocol.sha256,
            "source_revision": source_revision,
            "test_split_accessed": False,
            "base_model_weights_updated": False,
        })


class FrozenMemSkill:
    method = "memskill"

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
        self.memories = {
            str(value["memory_id"]): MemSkillItem.from_dict(value)
            for value in read_jsonl(self.root / "memories.jsonl")
        }
        self.index = DenseIndex.load(self.root / "memory_index")
        if (
            self.index.model_name != embedder.model_name
            or self.index.revision != embedder.revision
        ):
            raise MemSkillError("MemSkill query encoder differs from training")
        if int(self.manifest.get("memory_top_k", -1)) != 20:
            raise MemSkillError("MemSkill retrieval K drifted from the formal setting")
        self.embedder = embedder

    def retrieve(self, query: TaskQuery) -> MemoryContext:
        vectors = self.embedder.encode([query.state_text()])
        if len(vectors) != 1:
            raise MemSkillError("query encoder returned an invalid batch")
        matches = self.index.search(vectors[0], top_k=20, scope=query.scope)
        items = [{
            "memory_id": match.item_id,
            "similarity": round(match.score, 8),
            "content": self.memories[match.item_id].content,
            "source_outcome": (
                "success" if self.memories[match.item_id].source_reward >= 0.5 else "failure"
            ),
        } for match in matches]
        prefix = (
            "MEMSKILL FROZEN PROCEDURAL MEMORY\n"
            "Use relevant success memories as possible procedures and failure "
            "memories as warnings. Do not treat retrieval as proof; re-verify.\n"
        )
        maximum = int(self.protocol.document["maximum_memory_context_characters"])
        while items:
            text = prefix + json.dumps(
                items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if len(text) <= maximum:
                break
            items.pop()
        else:
            text = ""
        return MemoryContext(
            method=self.method,
            text=text,
            memory_item_ids=tuple(str(item["memory_id"]) for item in items),
            metadata={
                "retrieval": "frozen_dense_cosine_top20",
                "top_k_requested": 20,
                "top_k_injected": len(items),
                "scope": list(query.scope),
                "context_sha256": digest(text),
                "context_characters": len(text),
                "learning_during_test": False,
            },
        )
