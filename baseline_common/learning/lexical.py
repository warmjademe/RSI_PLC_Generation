from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]")


def tokens(text: str) -> list[str]:
    return [item.casefold() for item in TOKEN_PATTERN.findall(text)]


@dataclass(frozen=True)
class RankedItem:
    item_id: str
    score: float


class BM25Index:
    def __init__(
        self,
        item_ids: Sequence[str],
        documents: Sequence[str],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        if not item_ids or len(item_ids) != len(documents):
            raise ValueError("BM25 inputs have inconsistent lengths")
        self.item_ids = tuple(item_ids)
        self.documents = tuple(Counter(tokens(document)) for document in documents)
        self.lengths = tuple(sum(document.values()) for document in self.documents)
        self.average_length = sum(self.lengths) / len(self.lengths)
        self.k1 = k1
        self.b = b
        frequencies = Counter()
        for document in self.documents:
            frequencies.update(document.keys())
        size = len(self.documents)
        self.idf = {
            term: math.log(1 + (size - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in frequencies.items()
        }

    def search(
        self,
        query: str,
        *,
        top_k: int,
        allowed_ids: set[str] | None = None,
    ) -> list[RankedItem]:
        query_terms = Counter(tokens(query))
        matches = []
        for item_id, document, length in zip(
            self.item_ids, self.documents, self.lengths
        ):
            if allowed_ids is not None and item_id not in allowed_ids:
                continue
            score = 0.0
            for term, query_count in query_terms.items():
                frequency = document.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / max(self.average_length, 1)
                )
                score += self.idf.get(term, 0.0) * (
                    frequency * (self.k1 + 1) / denominator
                ) * query_count
            matches.append(RankedItem(item_id=item_id, score=score))
        return sorted(
            matches, key=lambda item: (-item.score, item.item_id)
        )[:top_k]


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[str]], *, constant: int = 60
) -> list[RankedItem]:
    scores: Counter[str] = Counter()
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, 1):
            scores[item_id] += 1.0 / (constant + rank)
    return [
        RankedItem(item_id=item_id, score=score)
        for item_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ]
