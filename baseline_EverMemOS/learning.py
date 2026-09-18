from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from baseline_common.learning.artifacts import (
    read_jsonl,
    seal_manifest,
    verify_manifest,
    write_json,
    write_jsonl,
)
from baseline_common.learning.embeddings import DenseIndex, Embedder, cosine, normalize
from baseline_common.learning.lexical import BM25Index, reciprocal_rank_fusion
from baseline_common.learning.models import MemoryContext, TaskQuery, TrajectoryEpisode
from baseline_common.learning.protocol import EvaluationProtocol, digest
from baseline_common.learning.provider import ModelCall, LearningProvider, summarize_model_audits


class EverMemOSError(RuntimeError):
    pass


@dataclass(frozen=True)
class MemCell:
    cell_id: str
    source_task_id: str
    target: str
    output_language: str
    episode: str
    atomic_facts: tuple[str, ...]
    foresight: tuple[dict[str, str], ...]
    metadata: dict[str, Any]

    def fact_text(self) -> str:
        return "\n".join(self.atomic_facts)

    def semantic_text(self) -> str:
        return "\n".join((self.episode, *self.atomic_facts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "cell_id": self.cell_id,
            "source_task_id": self.source_task_id,
            "target": self.target,
            "output_language": self.output_language,
            "episode": self.episode,
            "atomic_facts": list(self.atomic_facts),
            "foresight": list(self.foresight),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MemCell":
        facts = value.get("atomic_facts")
        foresight = value.get("foresight")
        if (
            value.get("schema_version") != 1
            or not isinstance(facts, list)
            or not facts
            or not all(isinstance(item, str) and item.strip() for item in facts)
            or not isinstance(foresight, list)
            or not all(isinstance(item, dict) for item in foresight)
        ):
            raise EverMemOSError("invalid MemCell schema")
        return cls(
            cell_id=str(value["cell_id"]),
            source_task_id=str(value["source_task_id"]),
            target=str(value["target"]),
            output_language=str(value["output_language"]),
            episode=str(value["episode"]),
            atomic_facts=tuple(item.strip() for item in facts),
            foresight=tuple({str(k): str(v) for k, v in item.items()} for item in foresight),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass
class MemScene:
    scene_id: str
    target: str
    output_language: str
    cell_ids: list[str]
    centroid: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scene_id": self.scene_id,
            "target": self.target,
            "output_language": self.output_language,
            "cell_ids": list(self.cell_ids),
            "centroid": list(self.centroid),
        }


def _call_audit(call: ModelCall, *, phase: str, source_task_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "phase": phase,
        "source_task_id": source_task_id,
        "provider": call.provider,
        "requested_model": call.requested_model,
        "resolved_model": call.resolved_model,
        "usage": call.usage,
        "latency_seconds": call.latency_seconds,
        "provider_request_count": call.provider_request_count,
        "resolved_models": list(call.resolved_models or (call.resolved_model,)),
        "content_sha256": digest(call.content),
    }


class EverMemOSTrainer:
    method = "evermemos"
    clustering_threshold = 0.70
    scene_top_k = 10
    episode_top_k = 10
    formation_parallelism = 6

    def __init__(
        self,
        *,
        protocol: EvaluationProtocol,
        embedder: Embedder,
        provider: LearningProvider,
    ):
        self.protocol = protocol
        self.embedder = embedder
        self.provider = provider

    @staticmethod
    def _narrative_messages(episode: TrajectoryEpisode) -> list[dict[str, str]]:
        return [{
            "role": "system",
            "content": (
                "You form an EverMemOS Episode from one complete PLC "
                "generation-debug trajectory. Write a concise third-person "
                "narrative that preserves the requirement pattern, attempted "
                "actions, compiler/verifier feedback, repair progression, final "
                "outcome, PLC target, and output language. Do not invent facts. "
                "Limit the Episode to 700 words. Return JSON: "
                "{\"episode\": string}."
            ),
        }, {
            "role": "user",
            "content": json.dumps(
                episode.compact_trace(), ensure_ascii=False, sort_keys=True
            ),
        }]

    @staticmethod
    def _structure_messages(
        episode: TrajectoryEpisode, narrative: str
    ) -> list[dict[str, str]]:
        return [{
            "role": "system",
            "content": (
                "Derive an EverMemOS MemCell from the supplied PLC Episode. "
                "Atomic facts must be discrete, verifiable statements grounded "
                "in compiler/verifier evidence or the terminal outcome. "
                "Foresight entries are forward-looking reuse conditions, not "
                "claims that a future program will pass. Return one to six "
                "atomic facts and at most three foresight items. Each item "
                "must have text, validity, and boundary. Return JSON exactly as "
                "{\"atomic_facts\": [string, ...], \"foresight\": "
                "[{\"text\": string, \"validity\": \"until_reverified\", "
                "\"boundary\": string}, ...]}."
            ),
        }, {
            "role": "user",
            "content": json.dumps({
                "target": episode.target,
                "output_language": episode.output_language,
                "episode": narrative,
            }, ensure_ascii=False, sort_keys=True),
        }]

    def _form_cell(self, episode: TrajectoryEpisode) -> tuple[MemCell, list[dict[str, Any]]]:
        narrative_doc, narrative_call = self.provider.json_chat(
            self._narrative_messages(episode), max_tokens=1200
        )
        narrative = narrative_doc.get("episode")
        if not isinstance(narrative, str) or not narrative.strip():
            raise EverMemOSError("narrative synthesis returned no Episode")
        structure_doc, structure_call = self.provider.json_chat(
            self._structure_messages(episode, narrative.strip()), max_tokens=1200
        )
        facts = structure_doc.get("atomic_facts")
        foresight = structure_doc.get("foresight")
        if (
            not isinstance(facts, list)
            or not facts
            or not all(isinstance(item, str) and item.strip() for item in facts)
            or not isinstance(foresight, list)
            or not all(isinstance(item, dict) for item in foresight)
        ):
            raise EverMemOSError("structural derivation returned an invalid schema")
        normalized_foresight = []
        for item in foresight:
            if item.get("validity") != "until_reverified":
                raise EverMemOSError("PLC foresight must expire on re-verification")
            normalized_foresight.append({
                "text": str(item.get("text", "")).strip(),
                "validity": "until_reverified",
                "boundary": str(item.get("boundary", "")).strip(),
            })
        cell_id = "cell_" + digest({
            "method": self.method,
            "source_task_id": episode.task_id,
            "run_key": episode.run_key,
        })[:24]
        cell = MemCell(
            cell_id=cell_id,
            source_task_id=episode.task_id,
            target=episode.target,
            output_language=episode.output_language,
            episode=narrative.strip(),
            atomic_facts=tuple(item.strip() for item in facts),
            foresight=tuple(normalized_foresight),
            metadata={
                "terminal_status": episode.terminal_status,
                "reward": episode.reward,
                "category_id": episode.category_id,
                "semantic_signature": episode.semantic_signature,
                "provenance": episode.provenance,
            },
        )
        return cell, [
            _call_audit(
                narrative_call,
                phase="narrative_synthesis",
                source_task_id=episode.task_id,
            ),
            _call_audit(
                structure_call,
                phase="structural_derivation",
                source_task_id=episode.task_id,
            ),
        ]

    def _cluster(
        self, cells: Sequence[MemCell], vectors: Sequence[Sequence[float]]
    ) -> list[MemScene]:
        scenes: list[MemScene] = []
        for cell, raw_vector in zip(cells, vectors):
            vector = normalize(raw_vector)
            eligible = [
                scene for scene in scenes
                if (scene.target, scene.output_language)
                == (cell.target, cell.output_language)
            ]
            nearest = max(
                eligible,
                key=lambda scene: (cosine(vector, scene.centroid), scene.scene_id),
                default=None,
            )
            similarity = (
                cosine(vector, nearest.centroid) if nearest is not None else -1.0
            )
            if nearest is not None and similarity >= self.clustering_threshold:
                old_count = len(nearest.cell_ids)
                nearest.cell_ids.append(cell.cell_id)
                nearest.centroid = normalize([
                    (nearest.centroid[index] * old_count + vector[index])
                    / (old_count + 1)
                    for index in range(len(vector))
                ])
            else:
                scene_id = "scene_" + digest({
                    "scope": [cell.target, cell.output_language],
                    "root_cell_id": cell.cell_id,
                })[:24]
                scenes.append(MemScene(
                    scene_id=scene_id,
                    target=cell.target,
                    output_language=cell.output_language,
                    cell_ids=[cell.cell_id],
                    centroid=vector,
                ))
        return scenes

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
        checkpoint_root = output_root / "formation_checkpoints"
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        checkpoint_documents: list[tuple[MemCell, list[dict[str, Any]]]] = []
        for path in sorted(checkpoint_root.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise EverMemOSError(
                    f"invalid formation checkpoint: {path}"
                ) from exc
            if (
                not isinstance(value, dict)
                or value.get("schema_version") != 1
                or not isinstance(value.get("cell"), dict)
                or not isinstance(value.get("model_calls"), list)
                or len(value["model_calls"]) != 2
            ):
                raise EverMemOSError(
                    f"invalid formation checkpoint schema: {path}"
                )
            cell = MemCell.from_dict(value["cell"])
            if value.get("source_task_id") != cell.source_task_id:
                raise EverMemOSError(
                    "formation checkpoint task identity mismatch"
                )
            checkpoint_documents.append((cell, value["model_calls"]))
        by_task = {
            cell.source_task_id: cell for cell, _ in checkpoint_documents
        }
        if len(by_task) != len(checkpoint_documents):
            raise EverMemOSError("formation checkpoints repeat a source task")
        ordered = sorted(episodes, key=lambda item: item.task_id)
        if len(ordered) != expected_count:
            raise EverMemOSError(
                f"EverMemOS expected {expected_count} trajectories"
            )
        pending = [
            episode for episode in ordered if episode.task_id not in by_task
        ]
        for offset in range(0, len(pending), self.formation_parallelism):
            batch = pending[offset:offset + self.formation_parallelism]
            errors: list[tuple[str, BaseException]] = []
            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = {
                    executor.submit(self._form_cell, episode): episode
                    for episode in batch
                }
                for future in as_completed(futures):
                    episode = futures[future]
                    try:
                        cell, audits = future.result()
                        write_json(
                            checkpoint_root / f"{episode.task_id}.json",
                            {
                                "schema_version": 1,
                                "source_task_id": episode.task_id,
                                "cell": cell.to_dict(),
                                "model_calls": audits,
                            },
                        )
                        checkpoint_documents.append((cell, audits))
                        by_task[episode.task_id] = cell
                    except BaseException as exc:
                        errors.append((episode.task_id, exc))
            if errors:
                task_id, error = sorted(errors, key=lambda item: item[0])[0]
                raise EverMemOSError(
                    f"MemCell formation failed for {task_id}: {error}"
                ) from error
        cells = tuple(by_task[episode.task_id] for episode in ordered)
        if len(cells) != expected_count:
            raise EverMemOSError("EverMemOS checkpoint is incomplete")
        audits_by_task = {
            cell.source_task_id: list(audits)
            for cell, audits in checkpoint_documents
        }
        if set(audits_by_task) != set(by_task):
            raise EverMemOSError(
                "formation call audit coverage is incomplete"
            )
        calls = tuple(
            call
            for episode in ordered
            for call in audits_by_task[episode.task_id]
        )
        write_jsonl(
            output_root / "memcells.jsonl",
            (cell.to_dict() for cell in cells),
        )
        write_jsonl(output_root / "model_calls.jsonl", calls)
        index = DenseIndex.build(
            item_ids=[cell.cell_id for cell in cells],
            texts=[cell.fact_text() for cell in cells],
            metadata=[{
                "target": cell.target,
                "output_language": cell.output_language,
                "source_task_id": cell.source_task_id,
            } for cell in cells],
            embedder=self.embedder,
        )
        index.save(output_root / "fact_dense_index")
        scenes = self._cluster(cells, index.vectors)
        write_jsonl(output_root / "memscenes.jsonl", (
            scene.to_dict() for scene in scenes
        ))
        return seal_manifest(output_root, {
            "schema_version": 1,
            "method": self.method,
            "method_variant": "episodes_only_reasoning",
            "lifecycle": [
                "episodic_trace_formation",
                "semantic_consolidation",
                "reconstructive_recollection",
            ],
            "cell_count": len(cells),
            "scene_count": len(scenes),
            "clustering_threshold": self.clustering_threshold,
            "scene_top_k": self.scene_top_k,
            "episode_top_k": self.episode_top_k,
            "hybrid_retrieval": "dense_bm25_rrf",
            "state_encoder": {
                "name": self.embedder.model_name,
                "revision": self.embedder.revision,
                "frozen": True,
            },
            **summarize_model_audits(calls),
            "formation_parallelism": self.formation_parallelism,
            "corpus_binding": corpus_binding,
            "protocol_sha256": self.protocol.sha256,
            "source_revision": source_revision,
            "paper_source_authority": "arxiv:2601.02163v2",
            "test_split_accessed": False,
            "base_model_weights_updated": False,
        })


class FrozenEverMemOS:
    method = "evermemos"

    def __init__(
        self,
        *,
        root: Path,
        protocol: EvaluationProtocol,
        embedder: Embedder,
        provider: LearningProvider | None,
    ):
        self.root = root.resolve()
        self.protocol = protocol
        self.manifest = verify_manifest(
            self.root,
            method=self.method,
            protocol_sha256=protocol.sha256,
        )
        self.cells = {
            cell.cell_id: cell
            for cell in (
                MemCell.from_dict(item)
                for item in read_jsonl(self.root / "memcells.jsonl")
            )
        }
        self.scenes = {
            str(item["scene_id"]): item
            for item in read_jsonl(self.root / "memscenes.jsonl")
        }
        self.cell_to_scene = {
            str(cell_id): scene_id
            for scene_id, scene in self.scenes.items()
            for cell_id in scene.get("cell_ids", [])
        }
        if set(self.cells) != set(self.cell_to_scene):
            raise EverMemOSError("MemScene membership does not cover MemCells")
        self.index = DenseIndex.load(self.root / "fact_dense_index")
        if (
            self.index.model_name != embedder.model_name
            or self.index.revision != embedder.revision
        ):
            raise EverMemOSError("EverMemOS query encoder differs from training")
        self.embedder = embedder
        self.provider = provider
        self.bm25 = BM25Index(
            list(self.cells),
            [self.cells[cell_id].fact_text() for cell_id in self.cells],
        )

    def _rank_cells(self, query: TaskQuery, query_text: str) -> list[str]:
        vectors = self.embedder.encode([query_text])
        if len(vectors) != 1:
            raise EverMemOSError("query encoder returned an invalid batch")
        allowed = {
            cell.cell_id for cell in self.cells.values()
            if (cell.target, cell.output_language) == query.scope
        }
        if not allowed:
            return []
        # Rank the complete eligible scope before scene aggregation. A hidden
        # Top-100 prefilter changes which MemScenes can win and is not part of
        # EverMemOS's documented hierarchical retrieval rule.
        dense = self.index.search(
            vectors[0], top_k=len(allowed), scope=query.scope
        )
        lexical = self.bm25.search(
            query_text,
            top_k=len(allowed),
            allowed_ids=allowed,
        )
        fused = reciprocal_rank_fusion([
            [item.item_id for item in dense],
            [item.item_id for item in lexical],
        ])
        # EverMemOS first scores scenes by maximum constituent relevance.
        score_by_cell = {item.item_id: item.score for item in fused}
        scene_scores = {
            scene_id: max(
                (score_by_cell.get(str(cell_id), 0.0) for cell_id in scene["cell_ids"]),
                default=0.0,
            )
            for scene_id, scene in self.scenes.items()
            if (scene.get("target"), scene.get("output_language")) == query.scope
        }
        selected_scenes = {
            scene_id for scene_id, _ in sorted(
                scene_scores.items(), key=lambda item: (-item[1], item[0])
            )[: int(self.manifest["scene_top_k"])]
        }
        return [
            item.item_id for item in fused
            if self.cell_to_scene[item.item_id] in selected_scenes
        ][: int(self.manifest["episode_top_k"])]

    @staticmethod
    def _sum_usage(calls: Sequence[ModelCall]) -> dict[str, float]:
        usage: dict[str, float] = {}
        for call in calls:
            for key, value in call.usage.items():
                usage[key] = usage.get(key, 0.0) + float(value)
        return usage

    def _agentic_rewrite(
        self,
        query: TaskQuery,
        cell_ids: Sequence[str],
    ) -> tuple[list[str], list[ModelCall]]:
        if self.provider is None:
            return list(cell_ids), []
        context = [
            {
                "episode": self.cells[cell_id].episode,
                "atomic_facts": list(self.cells[cell_id].atomic_facts),
            }
            for cell_id in cell_ids
        ]
        verdict, check_call = self.provider.json_chat([{
            "role": "system",
            "content": (
                "Judge only whether the retrieved PLC memories contain "
                "sufficient reusable evidence to guide a new implementation. "
                "Do not solve the task. Return JSON: {\"is_sufficient\": "
                "boolean, \"key_information\": string, "
                "\"missing_information\": string}."
            ),
        }, {
            "role": "user",
            "content": json.dumps({
                "query": query.state_text(), "retrieved": context
            }, ensure_ascii=False, sort_keys=True),
        }], max_tokens=900)
        if verdict.get("is_sufficient") is True:
            return list(cell_ids), [check_call]
        rewritten, rewrite_call = self.provider.json_chat([{
            "role": "system",
            "content": (
                "Generate exactly three distinct retrieval queries for missing "
                "PLC implementation/debugging evidence: one keyword query, one "
                "natural question, and one hypothetical statement. Return JSON "
                "{\"queries\": [string, string, string]}. Do not include an "
                "answer or candidate program."
            ),
        }, {
            "role": "user",
            "content": json.dumps({
                "original_query": query.state_text(),
                "key_information": verdict.get("key_information", ""),
                "missing_information": verdict.get("missing_information", ""),
            }, ensure_ascii=False, sort_keys=True),
        }], max_tokens=700)
        queries = rewritten.get("queries")
        if not isinstance(queries, list) or len(queries) != 3:
            raise EverMemOSError("query rewriting returned an invalid schema")
        merged = list(cell_ids)
        for text in queries:
            for cell_id in self._rank_cells(query, str(text)):
                if cell_id not in merged:
                    merged.append(cell_id)
        return merged[: int(self.manifest["episode_top_k"])], [
            check_call, rewrite_call
        ]

    def retrieve(self, query: TaskQuery) -> MemoryContext:
        selected = self._rank_cells(query, query.state_text())
        selected, calls = self._agentic_rewrite(query, selected)
        # The paper's default quantitative mode is Episodes-only. Atomic facts
        # drive retrieval, while foresight/profile are not injected.
        payload = [{
            "cell_id": cell_id,
            "scene_id": self.cell_to_scene[cell_id],
            "episode": self.cells[cell_id].episode,
        } for cell_id in selected]
        prefix = (
            "EVERMEMOS RECOLLECTION (Episodes-only reasoning mode)\n"
            "These are fallible training episodes selected through MemScenes. "
            "Use only applicable procedures and re-run all PLC verification.\n"
        )
        maximum = int(
            self.protocol.document["maximum_memory_context_characters"]
        )
        text = prefix + json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        while payload and len(text) > maximum:
            payload.pop()
            text = prefix + json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        if not payload:
            text = ""
        retained = tuple(str(item["cell_id"]) for item in payload)
        phases = ("retrieval_sufficiency_check", "retrieval_query_rewrite")
        call_audits = [{
            "phase": phases[index],
            "provider": call.provider,
            "requested_model": call.requested_model,
            "resolved_model": call.resolved_model,
            "resolved_models": list(
                call.resolved_models or (call.resolved_model,)
            ),
            "provider_request_count": call.provider_request_count,
            "usage": dict(call.usage),
            "latency_seconds": call.latency_seconds,
            "content_sha256": digest(call.content),
        } for index, call in enumerate(calls)]
        return MemoryContext(
            method=self.method,
            text=text,
            memory_item_ids=retained,
            retrieval_usage=self._sum_usage(calls),
            metadata={
                "scene_ids": sorted({
                    str(item["scene_id"]) for item in payload
                }),
                "agentic_call_count": len(calls),
                **summarize_model_audits(call_audits),
                "retrieval_model_calls": call_audits,
                "episodes_only": True,
                "context_sha256": digest(text),
                "context_characters": len(text),
            },
        )
