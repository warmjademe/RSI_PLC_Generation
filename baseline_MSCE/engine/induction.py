from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from typing import Any

from .prompts import (
    cognition_messages,
    extract_tagged_json,
    policy_induction_messages,
    skill_messages,
)
from .provider import OpenAIChatProvider, ProviderError
from .storage import MemoryStore


class InductionError(ValueError):
    pass


def backfill_values(
    length: int,
    reward: float | None,
    gamma: float = 0.9,
    reflection_weights: list[float] | None = None,
) -> list[float | None]:
    if length < 0:
        raise ValueError("trajectory length cannot be negative")
    if length == 0:
        return []
    if reward is None:
        return [None] * length
    if reflection_weights is not None:
        if len(reflection_weights) not in {length - 1, length}:
            raise ValueError(
                "reflection_weights must cover every non-terminal step"
            )
        if any(not 0.0 <= float(item) <= 1.0 for item in reflection_weights):
            raise ValueError("reflection weight is outside [0,1]")
    values = [0.0] * length
    values[-1] = float(reward)
    for index in range(length - 2, -1, -1):
        if reflection_weights is None:
            # Backward-compatible neutral fallback for historical online runs.
            distance = length - 1 - index
            alpha = 1.0 / (distance + 1.0)
        else:
            alpha = float(reflection_weights[index])
        values[index] = alpha * reward + (1.0 - alpha) * gamma * values[index + 1]
    return values


def _bounded_score(value: Any) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise InductionError(f"score outside [0,1]: {number}")
    return number


def validate_reflection(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise InductionError('reflection must be an object')
    document = dict(document)
    names = ('correctness', 'specificity', 'evidence_grounding', 'reusability')
    # Preserve scores actually supplied by the model; only normalize nesting.
    # Partial, conflicting or missing values must never become invented scores.
    if 'scores' not in document and all(key in document for key in names):
        document['scores'] = {key: document.pop(key) for key in names}
    elif 'scores' in document and any(key in document for key in names):
        raise InductionError('reflection has ambiguous nested and top-level scores')
    for key in ("summary", "causal_diagnosis", "reusable_lesson", "boundary", "scores"):
        if key not in document:
            raise InductionError(f"reflection is missing {key}")
    if not isinstance(document['scores'], dict):
        raise InductionError('reflection scores must be an object')
    scores = dict(document['scores'])
    for key in names:
        if key not in scores or isinstance(scores[key], bool):
            raise InductionError('reflection is missing a numeric score: '+key)
        try:
            scores[key] = _bounded_score(scores[key])
        except (TypeError, ValueError, OverflowError) as exc:
            raise InductionError('reflection has an invalid score: '+key) from exc
    document['scores'] = scores
    return document


def validate_policy(document: dict[str, Any], allowed_episode_ids: set[int]) -> dict[str, Any]:
    for key in ("trigger", "procedure", "verification", "boundary", "evidence_episode_ids", "confidence"):
        if key not in document:
            raise InductionError(f"policy is missing {key}")
    for key in ("procedure", "verification", "boundary", "evidence_episode_ids"):
        if not isinstance(document[key], list) or not document[key]:
            raise InductionError(f"policy {key} must be a non-empty list")
    evidence = {int(item) for item in document["evidence_episode_ids"]}
    if len(evidence) < 2 or not evidence.issubset(allowed_episode_ids):
        raise InductionError("policy evidence must cite at least two supplied episodes")
    document["evidence_episode_ids"] = sorted(evidence)
    document["confidence"] = _bounded_score(document["confidence"])
    return document


def validate_cognition(document: dict[str, Any], allowed_policy_ids: set[int]) -> dict[str, Any]:
    for key in ("statement", "applicability", "verification", "boundary", "evidence_policy_ids", "confidence"):
        if key not in document:
            raise InductionError(f"cognition is missing {key}")
    if not isinstance(document["statement"], str) or not document["statement"].strip():
        raise InductionError("cognition statement must be a non-empty string")
    document["statement"] = document["statement"].strip()
    for key in ("applicability", "verification", "boundary"):
        value = document[key]
        # A single string has the same meaning as a one-item list.  Normalize
        # that presentation-only variation, but reject mixed/non-string data.
        if isinstance(value, str) and value.strip():
            value = [value.strip()]
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)
        ):
            raise InductionError(
                f"cognition {key} must be a non-empty string list"
            )
        document[key] = [item.strip() for item in value]
    if not isinstance(document["evidence_policy_ids"], list):
        raise InductionError("cognition evidence_policy_ids must be a list")
    evidence = {int(item) for item in document["evidence_policy_ids"]}
    if len(evidence) < 2 or not evidence.issubset(allowed_policy_ids):
        raise InductionError("cognition must cite at least two supplied policies")
    document["evidence_policy_ids"] = sorted(evidence)
    document["confidence"] = _bounded_score(document["confidence"])
    return document


def validate_skill(document: dict[str, Any], policy: dict[str, Any], max_evidence: int) -> dict[str, Any]:
    for key in ("name", "description", "trigger", "procedure", "verification", "boundary", "evidence_episode_ids"):
        if key not in document:
            raise InductionError(f"skill is missing {key}")
    evidence = [int(item) for item in document["evidence_episode_ids"]]
    allowed = set(policy["evidence_episode_ids"])
    if not evidence or not set(evidence).issubset(allowed):
        raise InductionError("skill cites evidence outside its source policy")
    document["evidence_episode_ids"] = evidence[:max_evidence]
    return document


def _softmax_mean(values: list[float], temperature: float) -> float:
    if len(values) < 3:
        return statistics.fmean(values)
    weights = [math.exp(value / temperature) for value in values]
    return sum(value * weight for value, weight in zip(values, weights)) / sum(weights)


def policy_gain(observations: dict[str, list[float]], settings: dict[str, Any]) -> dict[str, Any]:
    with_values = observations["with"]
    without_values = observations["without"]
    if not with_values:
        return {"eligible": False, "reason": "no_with_policy_observation"}
    temperature = float(settings.get("gain_softmax_temperature", 0.5))
    prior_mean = float(settings.get("gain_prior_mean", 0.5))
    prior_weight = float(settings.get("gain_prior_weight", 5))
    with_mean = _softmax_mean(with_values, temperature)
    without_raw = statistics.fmean(without_values) if without_values else prior_mean
    without_blend = (
        len(without_values) * without_raw + prior_weight * prior_mean
    ) / (len(without_values) + prior_weight)
    gain = with_mean - without_blend
    stable = (
        len(with_values) >= 2
        and len(without_values) >= 2
        and (statistics.pvariance(with_values) if len(with_values) > 1 else 0.0) <= 0.25
    )
    return {
        "eligible": gain > 0.0 and stable,
        "gain": gain,
        "with_mean": with_mean,
        "without_raw_mean": without_raw,
        "without_blended_mean": without_blend,
        "n_with": len(with_values),
        "n_without": len(without_values),
        "stable": stable,
        "estimator": "softmax_mean_if_n_ge_3_else_arithmetic; without_prior_shrinkage",
    }


class InductionEngine:
    def __init__(self, store: MemoryStore, provider: OpenAIChatProvider, settings: dict[str, Any]):
        self.store = store
        self.provider = provider
        self.settings = settings

    @staticmethod
    def _record_operator_error(
        created: dict[str, list[Any]],
        *,
        stage: str,
        identity: str,
        exc: Exception,
    ) -> None:
        created["errors"].append({
            "stage": stage,
            "identity": identity,
            "error_type": type(exc).__name__,
            "message": str(exc)[:1000],
        })

    def _complete_recorded(
        self,
        created: dict[str, list[Any]],
        *,
        stage: str,
        identity: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> tuple[Any, dict[str, Any]]:
        request_bytes = json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            reply = self.provider.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except ProviderError as exc:
            created["calls"].append({
                "stage": stage,
                "identity": identity,
                "status": "provider_error",
                "request": messages,
                "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
                "error_type": type(exc).__name__,
                "message": str(exc)[:1000],
            })
            raise
        response_bytes = reply.text.encode("utf-8")
        call = {
            "stage": stage,
            "identity": identity,
            "status": "response_received",
            "validation_status": "pending",
            "request": messages,
            "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
            "response_text": reply.text,
            "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
            "provider": reply.provider,
            "model": reply.model,
            "latency_ms": reply.latency_ms,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "finish_reason": reply.finish_reason,
            "request_id": reply.request_id,
            "raw_usage": reply.raw_usage,
        }
        created["calls"].append(call)
        return reply, call

    def induce(self) -> dict[str, list[Any]]:
        created: dict[str, list[Any]] = {
            "policies": [], "cognition": [], "skills": [], "errors": [],
            "calls": [],
        }
        self._induce_policies(created)
        self._induce_cognition(created)
        self._crystallize_skills(created)
        return created

    def _induce_policies(self, created: dict[str, list[Any]]) -> None:
        existing_signatures = {policy["signature"] for policy in self.store.policies()}
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for episode in self.store.recent_episodes(limit=5000):
            if episode["split"] == "train" and episode["terminal_reward"] is not None:
                grouped[episode["signature"]].append(episode)
        minimum = int(self.settings.get("l2_min_distinct_episodes", 2))
        min_value = float(self.settings.get("l2_min_episode_value", 0.1))
        for signature, episodes in sorted(grouped.items()):
            if signature in existing_signatures:
                continue
            eligible = [episode for episode in episodes if episode["terminal_reward"] >= min_value]
            distinct_tasks = {episode["task_id"] for episode in eligible}
            if len(distinct_tasks) < minimum:
                continue
            evidence = eligible[: min(12, len(eligible))]
            call: dict[str, Any] | None = None
            try:
                reply, call = self._complete_recorded(
                    created,
                    stage="l2_policy_induction",
                    identity=signature,
                    messages=policy_induction_messages(signature, evidence),
                    temperature=0.1,
                    max_tokens=3000,
                )
                document = validate_policy(
                    extract_tagged_json(reply.text, "policy_json"),
                    {int(episode["id"]) for episode in evidence},
                )
                call["validation_status"] = "pass"
            except (ProviderError, ValueError) as exc:
                if call is not None:
                    call["validation_status"] = "fail"
                    call["validation_error_type"] = type(exc).__name__
                    call["validation_error"] = str(exc)[:1000]
                self._record_operator_error(
                    created,
                    stage="l2_policy_induction",
                    identity=signature,
                    exc=exc,
                )
                continue
            policy_id = self.store.add_policy(signature, document, reply.model)
            created["policies"].append(policy_id)

    def _induce_cognition(self, created: dict[str, list[Any]]) -> None:
        policies = self.store.policies()
        minimum = int(self.settings.get("l3_min_policies", 2))
        if len(policies) < minimum:
            return
        existing_evidence = {tuple(sorted(item["evidence_policy_ids"])) for item in self.store.cognition()}
        candidates = sorted(policies, key=lambda item: item["id"], reverse=True)[: min(8, len(policies))]
        evidence_ids = tuple(sorted(item["id"] for item in candidates))
        if evidence_ids in existing_evidence:
            return
        identity = ",".join(str(item) for item in evidence_ids)
        call: dict[str, Any] | None = None
        try:
            reply, call = self._complete_recorded(
                created,
                stage="l3_cognition_abstraction",
                identity=identity,
                messages=cognition_messages(candidates),
                temperature=0.1,
                max_tokens=2400,
            )
            document = validate_cognition(
                extract_tagged_json(reply.text, "cognition_json"),
                set(evidence_ids),
            )
            call["validation_status"] = "pass"
        except (ProviderError, ValueError) as exc:
            if call is not None:
                call["validation_status"] = "fail"
                call["validation_error_type"] = type(exc).__name__
                call["validation_error"] = str(exc)[:1000]
            self._record_operator_error(
                created,
                stage="l3_cognition_abstraction",
                identity=identity,
                exc=exc,
            )
            return
        cognition_id = self.store.add_cognition(document, reply.model)
        created["cognition"].append(cognition_id)

    def _crystallize_skills(self, created: dict[str, list[Any]]) -> None:
        existing_policy_ids = {skill["policy_id"] for skill in self.store.skills(states=("active", "probation", "archived"))}
        max_evidence = int(self.settings.get("max_skill_evidence", 6))
        for policy in self.store.policies():
            if policy["id"] in existing_policy_ids:
                continue
            gain = policy_gain(self.store.policy_gain_observations(policy["id"]), self.settings)
            if not gain.get("eligible"):
                continue
            identity = str(policy["id"])
            call: dict[str, Any] | None = None
            try:
                reply, call = self._complete_recorded(
                    created,
                    stage="skill_crystallization",
                    identity=identity,
                    messages=skill_messages(policy, gain),
                    temperature=0.1,
                    max_tokens=2600,
                )
                document = validate_skill(
                    extract_tagged_json(reply.text, "skill_json"),
                    policy,
                    max_evidence,
                )
                call["validation_status"] = "pass"
            except (ProviderError, ValueError) as exc:
                if call is not None:
                    call["validation_status"] = "fail"
                    call["validation_error_type"] = type(exc).__name__
                    call["validation_error"] = str(exc)[:1000]
                self._record_operator_error(
                    created,
                    stage="skill_crystallization",
                    identity=identity,
                    exc=exc,
                )
                continue
            skill_id = self.store.add_skill(
                policy["id"], document, gain, reply.model
            )
            created["skills"].append(skill_id)
