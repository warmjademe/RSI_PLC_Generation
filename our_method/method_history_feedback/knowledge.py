"""Scoped retrieval and evidence-gated, versioned claims (not learned weights)."""
import copy
import re

from baseline_common.errors import ProtocolError
from baseline_common.utils import canonical_json, object_hash
from .evidence import validate_citations


def _tokens(text):
    words = set(re.findall(r"[a-z_][a-z_0-9]*|[\u4e00-\u9fff]", text.lower()))
    return words - {"plc", "st", "iec", "the", "a", "to", "of", "and", "is", "in", "for", "的", "是"}


def retrieve(snapshot, task, scope, settings, feedback="", *, for_curation=False):
    if not for_curation and not settings["use_cross_task_knowledge"]:
        return []
    query = _tokens(task["requirement"] + " " + canonical_json(task["interface"]) + " " + feedback)
    ranked = []
    for record in snapshot["records"]:
        allowed = ("active", "draft") if for_curation else ("active",)
        if record["status"] not in allowed or record["scope"] != scope:
            continue
        terms = _tokens(" ".join([record["text"], *record["keywords"], *record["applies_when"]]))
        overlap = query & terms
        if overlap:
            score = len(overlap) / max(1, len(terms)) ** .5
            ranked.append((score, record["id"], record))
    selected, chars = [], 0
    for score, _, record in sorted(ranked, key=lambda x: (-x[0], x[1])):
        # No old full programs, requirements or raw tool records in generation memory.
        view = {k: copy.deepcopy(record[k]) for k in (
            "id", "version", "text", "applies_when", "does_not_apply_when", "checks",
            "keywords", "scope", "support_task_ids", "evidence_level")}
        view["evidence_attempt_ids"] = sorted({e["attempt_id"] for e in record["evidence"]})
        view["retrieval_score"] = round(score, 6)
        size = len(canonical_json(view))
        if chars + size > settings["knowledge_char_budget"]:
            continue
        selected.append(view)
        chars += size
        if len(selected) >= settings["max_knowledge_items"]:
            break
    return selected


def _strings(value, key, maximum=8):
    if (not isinstance(value, list) or not 1 <= len(value) <= maximum
            or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 1000 for v in value)):
        raise ProtocolError(key + " requires bounded, nonempty strings")


def validate_proposals(raw, available, parents, maximum):
    if not isinstance(raw, dict) or set(raw) != {"claims"} or not isinstance(raw["claims"], list):
        raise ProtocolError("curation must return {claims: [...]} only")
    if len(raw["claims"]) > maximum:
        raise ProtocolError("too many proposed claims")
    required = {"text", "applies_when", "does_not_apply_when", "checks", "keywords", "relation", "parent_id", "evidence"}
    proposals = []
    for item in raw["claims"]:
        if not isinstance(item, dict) or set(item) != required:
            raise ProtocolError("knowledge proposal schema mismatch")
        if not isinstance(item["text"], str) or not 1 <= len(item["text"].strip()) <= 1800:
            raise ProtocolError("knowledge text must be bounded and nonempty")
        for key in ("applies_when", "does_not_apply_when", "checks", "keywords"):
            _strings(item[key], key)
        if item["relation"] not in ("new", "supports", "refines", "contradicts"):
            raise ProtocolError("unknown knowledge relation")
        parent = item["parent_id"]
        if item["relation"] == "new":
            if parent is not None:
                raise ProtocolError("new claims cannot name a parent")
        elif not isinstance(parent, str) or parent not in parents:
            raise ProtocolError("parent must be an existing offered knowledge record")
        citations = validate_citations(item["evidence"], available)
        proposals.append({**copy.deepcopy(item), "evidence": citations,
                          "proposal_id": "p-" + object_hash(item)[:16]})
    if len({p["proposal_id"] for p in proposals}) != len(proposals):
        raise ProtocolError("duplicate proposal")
    return proposals


def adjudicate(proposals, raw, available, parents, scope, order, min_support_tasks):
    """A second LLM reviews meaning; deterministic gates only verify provenance.

    Exact quotations prove source existence, not causality or universal correctness.
    """
    if not isinstance(raw, dict) or set(raw) != {"reviews"} or not isinstance(raw["reviews"], list):
        raise ProtocolError("review must return {reviews: [...]} only")
    reviews = {}
    for review in raw["reviews"]:
        required = {"proposal_id", "verdict", "reason", "evidence"}
        if not isinstance(review, dict) or set(review) != required:
            raise ProtocolError("review schema mismatch")
        pid = review["proposal_id"]
        if not isinstance(pid, str) or pid in reviews:
            raise ProtocolError("duplicate/invalid reviewed proposal")
        if review["verdict"] not in ("supported", "insufficient", "reject"):
            raise ProtocolError("invalid review verdict")
        if not isinstance(review["reason"], str) or not 1 <= len(review["reason"].strip()) <= 2000:
            raise ProtocolError("review must explain its evidence assessment")
        validate_citations(review["evidence"], available)
        reviews[pid] = review
    if set(reviews) != {p["proposal_id"] for p in proposals}:
        raise ProtocolError("every proposal needs exactly one review")
    updates, decisions, touched = [], [], set()
    for proposal in proposals:
        review = reviews[proposal["proposal_id"]]
        if review["verdict"] == "reject":
            decisions.append({"proposal_id": proposal["proposal_id"], "status": "rejected", "reason": review["reason"]})
            continue
        evidence = []
        for item in proposal["evidence"] + review["evidence"]:
            if item not in evidence:
                evidence.append(item)
        if len(evidence) > 8:
            raise ProtocolError("combined claim/review evidence exceeds eight quotes")
        observation_ids = {e["attempt_id"] for e in evidence if e["field"] == "observation"}
        current_observations = {aid for aid in observation_ids if available[aid]["order"] == order}
        current_pass = any(available[aid]["passed"] for aid in current_observations)
        passing = {available[aid]["task_id"] for aid in observation_ids if available[aid]["passed"]}
        accepted = review["verdict"] == "supported" and bool(current_observations)
        relation, parent_id = proposal["relation"], proposal["parent_id"]
        parent = parents.get(parent_id)
        if parent and parent["scope"] != scope:
            raise ProtocolError("cannot extend knowledge across different validator scopes")
        # A support update preserves the exact existing rule; editing it requires refines.
        if relation == "supports" and any(proposal[k] != parent[k] for k in (
                "text", "applies_when", "does_not_apply_when", "checks", "keywords")):
            raise ProtocolError("supports must preserve the existing claim and its conditions")
        if relation == "supports" and accepted and current_pass:
            passing |= set(parent["support_task_ids"])
        active = accepted and current_pass and len(passing) >= min_support_tasks
        if relation == "contradicts":
            active = False  # A counterexample withdraws a rule; it does not prove its replacement.
            accepted = accepted and any(available[aid]["confirmed_failure"] for aid in current_observations)
        if parent_id in touched:
            raise ProtocolError("multiple updates to the same parent in one publication")
        if parent_id and accepted:
            touched.add(parent_id)
        if parent and accepted and relation in ("contradicts", "refines"):
            # Refinement only retires the old rule if the replacement meets admission gates.
            if relation == "contradicts" or active:
                updates.append({**copy.deepcopy(parent), "version": parent["version"] + 1,
                    "status": "disputed" if relation == "contradicts" else "superseded",
                    "evidence": evidence, "published_after_order": order,
                    "review": copy.deepcopy(review)})
        cid = parent_id if relation == "supports" and accepted and current_pass else "k-" + object_hash({"scope": scope, "proposal": proposal, "order": order})[:20]
        record = {k: copy.deepcopy(proposal[k]) for k in (
            "text", "applies_when", "does_not_apply_when", "checks", "keywords", "relation", "parent_id")}
        record.update(id=cid, version=parent["version"] + 1 if cid == parent_id else 1,
                      status="active" if active else "draft", evidence=evidence,
                      support_task_ids=sorted(passing), scope=copy.deepcopy(scope),
                      evidence_level="multiple_tasks_observed" if len(passing) >= 2 else "single_task_observed" if passing else "hypothesis_only",
                      published_after_order=order, review=copy.deepcopy(review))
        updates.append(record)
        decisions.append({"proposal_id": proposal["proposal_id"], "claim_id": cid, "status": record["status"], "reason": review["reason"]})
    return updates, decisions
