from __future__ import annotations

import json
import math
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model_name: str
    revision: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class SentenceTransformerEmbedder:
    """Frozen encoder used by formal dense-retrieval baselines.

    The model revision is mandatory so a later Hugging Face update cannot
    silently change retrieval.  The base LLM remains API-only and frozen.
    """

    def __init__(
        self,
        *,
        model_name: str,
        revision: str,
        batch_size: int = 16,
        device: str = "cpu",
    ):
        if not model_name or not revision:
            raise EmbeddingError("formal encoder requires model name and revision")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                "install sentence-transformers in the training environment"
            ) from exc
        self.model_name = model_name
        self.revision = revision
        self.batch_size = batch_size
        self.device = device
        self._model = SentenceTransformer(
            model_name,
            revision=revision,
            device=device,
        )

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(item) for item in vector] for vector in vectors]


class HuggingFaceMeanPoolEmbedder:
    """Pinned mean-pooled encoder matching MemSkill's Longformer path."""

    def __init__(
        self,
        *,
        model_name: str,
        revision: str,
        batch_size: int = 16,
        device: str = "cuda",
        maximum_tokens: int = 4096,
    ):
        if not model_name or not revision:
            raise EmbeddingError("formal encoder requires model name and revision")
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise EmbeddingError(
                "install torch and transformers in the formal training environment"
            ) from exc
        self.model_name = model_name
        self.revision = revision
        self.batch_size = batch_size
        self.device = device
        self.maximum_tokens = maximum_tokens
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_name, revision=revision
        )
        self._model = AutoModel.from_pretrained(
            model_name, revision=revision
        ).to(device)
        self._model.eval()

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        torch = self._torch
        results: list[list[float]] = []
        for offset in range(0, len(texts), self.batch_size):
            batch = list(texts[offset:offset + self.batch_size])
            encoded = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.maximum_tokens,
                return_tensors="pt",
            )
            encoded = {key: value.to(self.device) for key, value in encoded.items()}
            with torch.no_grad():
                output = self._model(**encoded)
                mask = encoded["attention_mask"].unsqueeze(-1).expand(
                    output.last_hidden_state.size()
                ).float()
                pooled = (
                    (output.last_hidden_state * mask).sum(dim=1)
                    / mask.sum(dim=1).clamp(min=1e-9)
                )
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            results.extend([
                [float(value) for value in vector]
                for vector in pooled.cpu().tolist()
            ])
        return results


def normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if norm <= 0 or not math.isfinite(norm):
        raise EmbeddingError("embedding has zero or invalid norm")
    return [float(value) / norm for value in vector]


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise EmbeddingError("cosine vectors have incompatible dimensions")
    return sum(float(a) * float(b) for a, b in zip(left, right))


@dataclass(frozen=True)
class DenseMatch:
    item_id: str
    score: float
    metadata: dict[str, Any]


class DenseIndex:
    def __init__(
        self,
        *,
        item_ids: tuple[str, ...],
        vectors: tuple[tuple[float, ...], ...],
        metadata: tuple[dict[str, Any], ...],
        model_name: str,
        revision: str,
    ):
        if not item_ids or len(item_ids) != len(vectors) or len(item_ids) != len(metadata):
            raise EmbeddingError("dense index arrays have inconsistent lengths")
        if len(set(item_ids)) != len(item_ids):
            raise EmbeddingError("dense index item IDs are not unique")
        dimension = len(vectors[0])
        if dimension < 1 or any(len(vector) != dimension for vector in vectors):
            raise EmbeddingError("dense index dimensions are inconsistent")
        self.item_ids = item_ids
        self.vectors = vectors
        self.metadata = metadata
        self.model_name = model_name
        self.revision = revision
        self.dimension = dimension

    @classmethod
    def build(
        cls,
        *,
        item_ids: Sequence[str],
        texts: Sequence[str],
        metadata: Sequence[dict[str, Any]],
        embedder: Embedder,
    ) -> "DenseIndex":
        if len(item_ids) != len(texts) or len(item_ids) != len(metadata):
            raise EmbeddingError("dense build inputs have inconsistent lengths")
        vectors = tuple(tuple(normalize(vector)) for vector in embedder.encode(texts))
        return cls(
            item_ids=tuple(item_ids),
            vectors=vectors,
            metadata=tuple(dict(item) for item in metadata),
            model_name=embedder.model_name,
            revision=embedder.revision,
        )

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        scope: tuple[str, str] | None = None,
    ) -> list[DenseMatch]:
        if top_k < 1:
            raise EmbeddingError("top_k must be positive")
        query = normalize(vector)
        matches = []
        for item_id, candidate, metadata in zip(
            self.item_ids, self.vectors, self.metadata
        ):
            if scope is not None and (
                metadata.get("target"), metadata.get("output_language")
            ) != scope:
                continue
            matches.append(DenseMatch(
                item_id=item_id,
                score=cosine(query, candidate),
                metadata=dict(metadata),
            ))
        return sorted(
            matches,
            key=lambda match: (-match.score, match.item_id),
        )[:top_k]

    def save(self, root: Path) -> dict[str, Any]:
        root.mkdir(parents=True, exist_ok=True)
        vector_path = root / "embeddings.f32"
        values = array("f")
        for vector in self.vectors:
            values.extend(vector)
        with vector_path.open("wb") as handle:
            values.tofile(handle)
        index = {
            "schema_version": 1,
            "model_name": self.model_name,
            "revision": self.revision,
            "normalization": "l2",
            "dtype": "float32-little-native",
            "dimension": self.dimension,
            "count": len(self.item_ids),
            "item_ids": list(self.item_ids),
            "metadata": list(self.metadata),
            "vector_file": vector_path.name,
        }
        (root / "dense_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return index

    @classmethod
    def load(cls, root: Path) -> "DenseIndex":
        index = json.loads((root / "dense_index.json").read_text(encoding="utf-8"))
        if index.get("schema_version") != 1 or index.get("normalization") != "l2":
            raise EmbeddingError("unsupported dense index schema")
        count = int(index.get("count", -1))
        dimension = int(index.get("dimension", -1))
        values = array("f")
        with (root / str(index.get("vector_file", ""))).open("rb") as handle:
            values.fromfile(handle, count * dimension)
            if handle.read(1):
                raise EmbeddingError("dense vector file has trailing bytes")
        if len(values) != count * dimension:
            raise EmbeddingError("dense vector file is truncated")
        vectors = tuple(
            tuple(float(value) for value in values[offset:offset + dimension])
            for offset in range(0, len(values), dimension)
        )
        return cls(
            item_ids=tuple(str(item) for item in index.get("item_ids", [])),
            vectors=vectors,
            metadata=tuple(dict(item) for item in index.get("metadata", [])),
            model_name=str(index.get("model_name", "")),
            revision=str(index.get("revision", "")),
        )
