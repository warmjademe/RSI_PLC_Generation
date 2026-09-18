from __future__ import annotations

import re
from typing import Any

from .models import RetrievedKnowledge, Task
from .storage import MemoryStore


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]+|[\u4e00-\u9fff]{2,}")
VALID_TARGETS = {"DVP48ES300R", "AS228T-A"}
VALID_OUTPUT_LANGUAGES = {"st", "ld"}


def tokens(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        text = " ".join(str(item) for item in value)
    elif isinstance(value, dict):
        text = " ".join(f"{key} {item}" for key, item in value.items())
    else:
        text = str(value)
    return {match.group(0).lower() for match in TOKEN_RE.finditer(text)}


def jaccard(left: Any, right: Any) -> float:
    a, b = tokens(left), tokens(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def signature_scope(signature: str) -> tuple[str, str] | None:
    """Return the declared evaluation track, rejecting partial/invalid scopes.

    Signatures written before four-track evaluation had no scope suffix.  They
    remain mutually compatible as ``unspecified`` knowledge, but they can never
    match a target/language-scoped formal-evaluation task.
    """
    parts = str(signature).split("|")
    targets = [part.removeprefix("target=") for part in parts if part.startswith("target=")]
    languages = [
        part.removeprefix("language=")
        for part in parts
        if part.startswith("language=")
    ]
    if not targets and not languages:
        return ("unspecified", "unspecified")
    if len(targets) != 1 or len(languages) != 1:
        return None
    target, language = targets[0], languages[0]
    if (target, language) == ("unspecified", "unspecified"):
        return (target, language)
    if target not in VALID_TARGETS or language not in VALID_OUTPUT_LANGUAGES:
        return None
    return (target, language)


def same_evaluation_scope(left_signature: str, right_signature: str) -> bool:
    left = signature_scope(left_signature)
    right = signature_scope(right_signature)
    return left is not None and right is not None and left == right


class Retriever:
    def __init__(self, store: MemoryStore, settings: dict[str, Any]):
        self.store = store
        self.threshold = float(settings.get("semantic_similarity_threshold", 0.62))
        self.skill_k = int(settings.get("retrieve_skills", 3))

    def retrieve(self, task: Task) -> RetrievedKnowledge:
        query = task.retrieval_text
        policies_from_store = self.store.policies()
        policy_by_id = {policy["id"]: policy for policy in policies_from_store}

        skills = []
        for skill in self.store.skills(states=("active", "probation")):
            source_policy = policy_by_id.get(skill["policy_id"])
            if source_policy is None or not same_evaluation_scope(
                task.signature, source_policy["signature"]
            ):
                continue
            score = jaccard(query, [skill["trigger"], skill["description"]])
            if score >= self.threshold or skill["reliability"] >= 0.8:
                skills.append((score * skill["reliability"], skill))
        skills.sort(key=lambda item: (item[0], item[1]["id"]), reverse=True)

        policies = []
        for policy in policies_from_store:
            if not same_evaluation_scope(task.signature, policy["signature"]):
                continue
            exact = policy["signature"] == task.signature
            score = jaccard(query, [policy["trigger"], policy["verification"]])
            if exact or score >= self.threshold:
                policies.append((1.0 if exact else score, policy))
        policies.sort(key=lambda item: (item[0], item[1]["confidence"], item[1]["id"]), reverse=True)

        traces = []
        for episode in self.store.recent_episodes(limit=250):
            if episode["split"] != "train" or episode["terminal_reward"] is None:
                continue
            if not same_evaluation_scope(task.signature, episode["signature"]):
                continue
            exact = episode["signature"] == task.signature
            score = jaccard(query, episode["retrieval_text"])
            if exact or score >= self.threshold:
                traces.append((1.0 if exact else score, {
                    "episode_id": episode["id"],
                    "task_id": episode["task_id"],
                    "terminal_status": episode["terminal_status"],
                    "terminal_reward": episode["terminal_reward"],
                    "reflection": episode["reflection"],
                    "feedback_kinds": episode["feedback_kinds"],
                }))
        traces.sort(key=lambda item: (item[0], item[1]["episode_id"]), reverse=True)

        cognition = []
        for item in self.store.cognition():
            evidence_scopes = {
                signature_scope(policy_by_id[policy_id]["signature"])
                for policy_id in item["evidence_policy_ids"]
                if policy_id in policy_by_id
            }
            if signature_scope(task.signature) not in evidence_scopes:
                continue
            score = jaccard(query, [item["statement"], item["applicability"]])
            if score >= self.threshold or "ISPSoft" in item["statement"] or "IEC-ST" in item["statement"]:
                cognition.append((score, item))
        cognition.sort(key=lambda item: (item[0], item[1]["confidence"]), reverse=True)

        selected_skills = [item[1] for item in skills[: self.skill_k]]
        selected_skill_policies = {item["policy_id"] for item in selected_skills}
        selected_policies = [item[1] for item in policies if item[1]["id"] not in selected_skill_policies][:3]
        return RetrievedKnowledge(
            skills=selected_skills,
            policies=selected_policies,
            traces=[item[1] for item in traces[:3]],
            cognition=[item[1] for item in cognition[:2]],
        )
