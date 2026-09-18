from __future__ import annotations

import json
import re
from typing import Any

from .models import RetrievedKnowledge, Task


SYSTEM_PROMPT = """You are an IEC 61131-3 Structured Text engineer targeting Delta DVP48ES300R through ISPSoft 3.24.
Generate exactly one complete FUNCTION_BLOCK. Preserve the public interface, names, directions, and types exactly. Use portable IEC-ST Core syntax only; do not use physical addresses, vendor pragmas, Markdown fences, prose outside the required tags, or a PROGRAM wrapper.

Reason explicitly about PLC scan order, retained variables, reset priority, safe output isolation, integer/real bounds, timers, and edge semantics. Treat supplied memory as fallible evidence: apply it only when its trigger matches, run its verification checks, and respect its boundary conditions.

Return:
<repair_hypothesis>one concise sentence, or initial implementation</repair_hypothesis>
<target_requirements>comma-separated requirement IDs</target_requirements>
<st_program>the complete FUNCTION_BLOCK</st_program>
"""


def _knowledge_text(knowledge: RetrievedKnowledge) -> str:
    sections: list[str] = []
    for label, records in (
        ("ACTIVE SKILLS", knowledge.skills),
        ("RELEVANT POLICIES", knowledge.policies),
        ("SIMILAR SUCCESS/FAILURE TRACES", knowledge.traces),
        ("ENVIRONMENT COGNITION", knowledge.cognition),
    ):
        if not records:
            continue
        sections.append(label)
        for record in records:
            payload = {key: value for key, value in record.items() if key not in {"embedding", "raw_response"}}
            sections.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return "\n".join(sections) if sections else "No prior memory matched this task."


def generation_messages(
    task: Task,
    knowledge: RetrievedKnowledge,
    feedback: str | None,
    previous_candidate: str | None,
) -> list[dict[str, str]]:
    parts = [
        "TASK REQUIREMENTS\n" + task.requirement,
        "PUBLIC INTERFACE\n" + task.interface,
        "RETRIEVED EXTERNAL MEMORY\n" + _knowledge_text(knowledge),
    ]
    if feedback is not None:
        parts.extend([
            "PREVIOUS CANDIDATE\n" + (previous_candidate or "<unavailable>"),
            "CONFIRMED VISIBLE ORACLE FEEDBACK\n" + feedback,
            "Repair only confirmed defects. Do not infer hidden test values.",
        ])
    else:
        parts.append("Produce the initial candidate and check every numbered requirement before returning it.")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def extract_st_program(text: str) -> str:
    match = re.search(r"<st_program>\s*(.*?)\s*</st_program>", text, flags=re.DOTALL | re.IGNORECASE)
    value = match.group(1).strip() if match else text.strip()
    fenced = re.fullmatch(r"```(?:st|iecst|text)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        value = fenced.group(1).strip()
    return value + ("\n" if value and not value.endswith("\n") else "")


def reflection_messages(
    task: Task,
    attempts: list[dict[str, Any]],
    terminal_reward: float,
) -> list[dict[str, str]]:
    compact = []
    for item in attempts:
        compact.append({
            "attempt": item["attempt"],
            "hypothesis": item.get("hypothesis", ""),
            "oracle_status": item["oracle_status"],
            "feedback": item["feedback"][:1600],
        })
    prompt = {
        "task_id": task.task_id,
        "category": task.metadata.get("category"),
        "control_patterns": task.metadata.get("retrieval", {}).get("control_patterns", []),
        "terminal_reward": terminal_reward,
        "trajectory": compact,
    }
    return [
        {"role": "system", "content": """Score and reflect on one PLC generation trajectory. Return only <reflection_json>{JSON}</reflection_json>. JSON fields: summary, causal_diagnosis, reusable_lesson, boundary, and scores with correctness, specificity, evidence_grounding, reusability each in [0,1]. Do not invent evidence or hidden tests."""},
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]


def extract_tagged_json(text: str, tag: str) -> dict[str, Any]:
    """Extract one JSON object while tolerating presentation-only wrappers.

    DeepSeek Flash occasionally substitutes the generic ``<json>`` tag for
    the requested stage-specific tag.  That is not a semantic failure.  The
    extractor therefore accepts the requested tag, a generic JSON tag, a
    Markdown JSON fence, or one embedded JSON object, in that order.  Schema
    and evidence validation still happen in the caller, so this does not
    repair or silently accept malformed induction content.
    """

    candidates: list[str] = []
    for candidate_tag in (tag, "json"):
        match = re.search(
            fr"<{re.escape(candidate_tag)}>\s*(.*?)\s*</{re.escape(candidate_tag)}>",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if match:
            candidates.append(match.group(1))
    fenced = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)

    errors: list[json.JSONDecodeError] = []
    decoder = json.JSONDecoder()
    for payload in candidates:
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            errors.append(exc)
            # Some providers prepend or append a short explanation despite
            # the response contract.  Decode the first syntactically complete
            # object; downstream validators reject the wrong schema/evidence.
            for start, character in enumerate(payload):
                if character != "{":
                    continue
                try:
                    value, _end = decoder.raw_decode(payload[start:])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
            continue
        if not isinstance(value, dict):
            raise ValueError("tagged JSON response is not an object")
        return value
    if errors:
        raise errors[-1]
    raise ValueError("tagged JSON response does not contain an object")


def policy_induction_messages(signature: str, episodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    evidence = []
    for episode in episodes:
        evidence.append({
            "episode_id": episode["id"],
            "task_id": episode["task_id"],
            "terminal_status": episode["terminal_status"],
            "terminal_reward": episode["terminal_reward"],
            "reflection": episode.get("reflection", {}),
            "feedback_kinds": episode.get("feedback_kinds", []),
        })
    return [
        {"role": "system", "content": """Induce one evidence-grounded PLC repair/generation policy from multiple distinct episodes. Return only <policy_json>{JSON}</policy_json> with fields trigger, procedure (array), verification (array), boundary (array), evidence_episode_ids (array), confidence in [0,1]. The trigger must state when the policy applies; boundaries must state when it must not be used. Do not reproduce a task answer."""},
        {"role": "user", "content": json.dumps({"signature": signature, "episodes": evidence}, ensure_ascii=False)},
    ]


def cognition_messages(policies: list[dict[str, Any]]) -> list[dict[str, str]]:
    payload = [{key: value for key, value in policy.items() if key in {"id", "trigger", "verification", "boundary", "confidence"}} for policy in policies]
    return [
        {"role": "system", "content": """Abstract one environment cognition supported by at least two supplied PLC policies. Return only <cognition_json>{JSON}</cognition_json>. The JSON object must contain: statement (non-empty string), applicability (non-empty array of strings), verification (non-empty array of strings), boundary (non-empty array of strings), evidence_policy_ids (array containing at least two supplied integer policy IDs), and confidence (number in [0,1]). Keep ISPSoft/COMMGR facts separate from IEC-ST semantic facts, cite only supplied policy IDs, and do not claim hardware validation."""},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def skill_messages(policy: dict[str, Any], gain: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": """Crystallize an evidence-grounded PLC policy into a compact reusable skill. Return only <skill_json>{JSON}</skill_json> with fields name, description, trigger, procedure, verification, boundary, evidence_episode_ids. Preserve limitations and do not include credentials, paths, hidden test values, or full task solutions."""},
        {"role": "user", "content": json.dumps({"policy": policy, "gain": gain}, ensure_ascii=False)},
    ]
