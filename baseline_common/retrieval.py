"""Deterministic BM25 replacement for services used in the original baselines."""
from __future__ import annotations

import math
import re
from collections import Counter


def _tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", text.casefold())
    for span in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.extend(span[i:i + 2] for i in range(len(span) - 1))
    return tokens


def rank_records(query: str, records: list[dict], limit: int = 5) -> list[dict]:
    if not records or limit <= 0:
        return []
    fields = ("id", "name", "requirement", "description", "summary", "keywords", "plan", "parameters", "types")
    docs = [_tokens(" ".join(str(r.get(k, "")) for k in fields)) for r in records]
    counts = [Counter(doc) for doc in docs]
    avg = sum(map(len, docs)) / len(docs) or 1
    frequency = Counter(token for doc in docs for token in set(doc))
    scores = []
    for idx, count in enumerate(counts):
        score = 0.0
        for token in set(_tokens(query)):
            freq = count[token]
            if freq:
                idf = math.log(1 + (len(docs) - frequency[token] + .5) / (frequency[token] + .5))
                score += idf * freq * 2.5 / (freq + 1.5 * (.25 + .75 * len(docs[idx]) / avg))
        scores.append((score, idx))
    return [dict(records[idx]) for _, idx in sorted(scores, key=lambda x: (-x[0], x[1]))[:limit]]
