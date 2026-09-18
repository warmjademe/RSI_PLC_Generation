"""Source-bound observations. LLM claims cannot author or alter tool verdicts."""
import copy

from baseline_common.errors import ProtocolError
from baseline_common.utils import canonical_json, content_hash, object_hash


def contract_hash(task):
    return object_hash({k: task.get(k) for k in (
        "id", "requirement", "target", "interface", "files", "entry_file", "public_properties")})


def attempt_record(task, order, candidate, code, checks, *, change_summary="", response_error="",
                   required_stages=("compile", "runtime", "formal")):
    required_stages = tuple(required_stages)
    if required_stages not in (("compile", "runtime"), ("compile", "runtime", "formal")):
        raise ProtocolError("unsupported required validation stages")
    for check in checks:
        if check.get("candidate_id") != candidate or check.get("code_hash") != content_hash(code):
            raise ProtocolError("attempt receipt does not bind the current candidate")
    latest = {c["stage"]: c for c in checks}
    statuses = {stage: check["status"] for stage, check in latest.items()}
    if set(statuses) - set(required_stages):
        raise ProtocolError("attempt contains a check outside its validation protocol")
    passed = all(statuses.get(s) == "pass" for s in required_stages)
    if passed and not all(c.get("evidence", {}).get("executed") is True for c in latest.values()):
        raise ProtocolError("passing evidence requires executed checks")
    observation = canonical_json([
        {"stage": c["stage"], "status": c["status"], "diagnostics": c.get("diagnostics", [])}
        for c in checks])
    return {"id": f"t{order:04d}-a{candidate:02d}", "task_id": task["id"], "order": order,
            "candidate": candidate, "contract_hash": contract_hash(task),
            "requirement": task["requirement"], "interface": copy.deepcopy(task["interface"]),
            "code": code, "code_hash": content_hash(code), "checks": copy.deepcopy(checks),
            "observation": observation, "passed": passed, "required_stages": list(required_stages),
            "confirmed_failure": any(c["status"] == "fail" for c in checks),
            "change_summary": change_summary, "response_error": response_error}


def evidence_view(record):
    """Exact bounded strings; quotes are checked against these displayed fields."""
    return {k: record[k] for k in ("id", "task_id", "order", "code_hash", "passed", "confirmed_failure")} | {
        "requirement": record["requirement"][:4000], "code": record["code"][:6000],
        "observation": record["observation"][:6000]}


def validate_citations(citations, available):
    if not isinstance(citations, list) or not 1 <= len(citations) <= 8:
        raise ProtocolError("one to eight evidence quotations required")
    cleaned = []
    for citation in citations:
        if not isinstance(citation, dict) or set(citation) != {"attempt_id", "field", "quote"}:
            raise ProtocolError("evidence needs exactly attempt_id, field, quote")
        if not isinstance(citation["attempt_id"], str):
            raise ProtocolError("evidence attempt_id must be a string")
        record = available.get(citation["attempt_id"])
        field, quote = citation["field"], citation["quote"]
        if (record is None or field not in ("requirement", "code", "observation")
                or not isinstance(quote, str) or not 8 <= len(quote) <= 1500
                or quote not in record[field]):
            raise ProtocolError("quotation is not present in the supplied evidence")
        cleaned.append(copy.deepcopy(citation))
    return cleaned


def validate_reflection(raw, current, available):
    keys = {"assumption", "proposed_change", "prediction", "confidence", "evidence"}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ProtocolError("reflection schema mismatch")
    for key in ("assumption", "proposed_change", "prediction"):
        if not isinstance(raw[key], str) or not 1 <= len(raw[key].strip()) <= 2000:
            raise ProtocolError("reflection requires bounded, concrete text")
    if raw["confidence"] not in ("low", "medium", "high"):
        raise ProtocolError("invalid reflection confidence")
    citations = validate_citations(raw["evidence"], available)
    if current["id"] not in {e["attempt_id"] for e in citations}:
        raise ProtocolError("reflection must cite the current attempt")
    return {**copy.deepcopy(raw), "evidence": citations, "status": "unverified_local_hypothesis"}
