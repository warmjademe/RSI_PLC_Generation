"""Two distinct updates: test-driven local revision and stage-evidenced memory.

Tool observations establish a transition, not a causal proof of a general rule.
All raw receipts remain in the original attempt records; views are supplementary.
"""
import copy
import difflib
import re

from baseline_common.errors import BudgetExceeded, ModelResponseError, ProtocolError, ProviderError
from baseline_common.utils import canonical_json, object_hash

REVISION = "two_level_feedback_20260915_v1"
ROLE_LIMITS = {"plc.generate": 16384, "knowledge.curate": 4096, "knowledge.review": 4096}

GENERATE = """Generate a complete IEC 61131-3 ST function block satisfying the supplied
requirement, exact interface and ALL scan tests. Return ONLY a JSON object with
code (complete source, no markdown), change_summary (at most 800 characters:
observed failure -> tentative cause -> specific change -> expected test effect),
applicability_notes (at most 500 characters), used_knowledge_ids (offered IDs only).
The actual evaluator is MatIEC; use (* ... *) comments, not // comments.
Current-task diagnostics are tool facts; previous explanations and historical
rules are fallible hypotheses. Diagnose the earliest compiler error with its
source context before interpreting cascading errors. For runtime failures trace
the supplied case from its fresh state, including preceding scans, input updates,
priority, reset, latch and equality boundaries. Preserve passing behavior.
If mode=rebuild, construct a different implementation from the requirement and
tests; do not repeat the failed source or its unsupported explanation. If
mode=restore_best, repair the supplied best-stage source, avoiding the regression.
No formal verification is requested. Do not manufacture test results or add code
that recognizes test IDs. A candidate passes only through external tools.
"""

CURATE = """Extract at most three reusable CONDITIONAL PLC rules from the supplied
verified stage transitions. Return {claims:[{transition_id,text,applies_when,
does_not_apply_when,checks,keywords}]}. Each list contains 1-4 short strings.
text <=800 characters, each list item <=240. Cite only the supplied transition ID;
never transcribe evidence quotations. Explain when the changed code is applicable,
what to implement, exceptions, and the test that can refute the rule. A compile
transition supports syntax/compilation observations only, not runtime correctness.
A multi-edit transition does not isolate the causal edit. Do not claim general
proof, copy task-specific variable names/constants, or restate the whole solution.
Return an empty claims list when no bounded reusable rule is supported.
"""

REVIEW = """Review each proposed conditional PLC rule against its cited source
transition, before/after code diff and actual tool facts. Return
{reviews:[{proposal_id,verdict,reason}]}; verdict is supported, insufficient, or
reject; reason <=500 characters. Review ALL proposals exactly once. The program
has checked stage outcomes and evidence IDs, not causality. Reject invented causes,
overbroad conditions, omitted exceptions, or a runtime claim based only on compile
success. Several simultaneous edits usually give insufficient causal evidence;
support only a narrowly scoped observation warranted by the diff and tests.
"""


def _text(maximum):
    return {"type": "string", "minLength": 1, "maxLength": maximum}


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def response_schema(role):
    strings = {"type": "array", "items": _text(240), "minItems": 1, "maxItems": 4}
    if role == "plc.generate":
        return _object({"code": _text(120000), "change_summary": _text(800),
            "applicability_notes": _text(500),
            "used_knowledge_ids": {"type": "array", "items": _text(64), "maxItems": 8}})
    if role == "knowledge.curate":
        return _object({"claims": {"type": "array", "maxItems": 3, "items": _object({
            "transition_id": _text(80), "text": _text(800),
            **{k: strings for k in ("applies_when", "does_not_apply_when", "checks", "keywords")}})}})
    if role == "knowledge.review":
        return _object({"reviews": {"type": "array", "maxItems": 3, "items": _object({
            "proposal_id": _text(80), "verdict": {"type": "string", "enum": ["supported", "insufficient", "reject"]},
            "reason": _text(500)})}})
    raise ProtocolError("unsupported two-level model role")


def apply_complete(raw):
    """Full replacement has no edit base; tool receipts bind the complete source."""
    schema = response_schema('plc.generate')
    if set(raw) != set(schema['properties']):
        raise ModelResponseError('two-level generation requires complete code and the three decision fields')
    for key in ('code', 'change_summary', 'applicability_notes'):
        if not isinstance(raw[key], str) or not 1 <= len(raw[key].strip()) <= schema['properties'][key]['maxLength']:
            raise ModelResponseError('invalid or overlong generation field: '+key)
    return raw['code'], {'representation': 'complete_code', 'edit_count': None,
                         'binding': 'complete source is hashed and bound to every tool receipt'}


def stages(attempt):
    return {c["stage"]: c for c in attempt["checks"]}


def _diagnostic(value):
    # Keep structured dictionaries intact: no truncated JSON blobs. Only large
    # individual strings/lists are bounded, with an explicit omission marker.
    if isinstance(value, dict):
        return {k: _diagnostic(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_diagnostic(v) for v in value[:12]] + ([{"omitted_items": len(value)-12}] if len(value)>12 else [])
    if isinstance(value, str):
        value = re.sub(r"(?:/[^\s:]+)+/candidate\.st", "candidate.st", value)
        return value if len(value) <= 5000 else value[:5000] + "\n[diagnostic excerpt; full original retained in receipts]"
    return value


def feedback_view(attempt):
    checks = stages(attempt)
    diagnostics = [{"stage": s, "status": c["status"], "diagnostics": _diagnostic(c.get("diagnostics", []))}
                   for s, c in checks.items()]
    traces = {}
    for view in diagnostics:
        report = checks[view['stage']].get('evidence', {}).get('tool_report', {})
        view['test_counts'] = {k: report[k] for k in ('assertions', 'failed_assertions', 'cycles') if k in report}
        for item in view['diagnostics']:
            if not isinstance(item, dict) or 'input_steps' not in item:
                continue
            trace = {k: item.pop(k) for k in ('input_steps', 'initial_state', 'input_steps_start_index', 'earlier_inputs_omitted') if k in item}
            trace_id = 'trace-' + object_hash(trace)[:12]
            traces[trace_id] = trace
            item['input_trace_id'] = trace_id
    lines = attempt["code"].splitlines()
    error_lines = [int(n) for n in re.findall(r"candidate\.st:(\d+)", canonical_json(diagnostics))][:4]
    excerpts = [{"first_line": max(1, n-3), "lines": lines[max(0,n-4):n+2]}
                for n in error_lines]
    return {"attempt_id": attempt["id"], "code_hash": attempt["code_hash"],
            "stage_results": diagnostics, "input_traces": traces, "compiler_source_context": excerpts,
            "previous_change_hypothesis": attempt["change_summary"],
            "response_error": attempt["response_error"]}


def fingerprint(attempt):
    facts = feedback_view(attempt)["stage_results"]
    # Line shifts and per-attempt paths must not make the same failure seem new.
    text = re.sub(r"candidate\.st:\d+(?::\d+)?", "candidate.st:LINE", canonical_json(facts))
    return object_hash(text)


def quality(attempt):
    state = {s: c["status"] for s, c in stages(attempt).items()}
    runtime = stages(attempt).get('runtime', {})
    failed = runtime.get('evidence', {}).get('tool_report', {}).get('failed_assertions')
    # Counts come from the fixed full test suite, not from LLM descriptions.
    failed = failed if type(failed) is int else len(runtime.get('diagnostics', []))
    return (int(state.get("compile") == "pass"), int(state.get("runtime") == "pass"), -failed)


def generation_payload(task, code, attempts, offered, settings, response_error):
    local = attempts if settings["use_task_feedback"] else []
    observed = [a for a in local if a["checks"]]
    mode, source, best = "initial", code if local else "", None
    duplicate = bool(observed and any(a["code_hash"] == observed[-1]["code_hash"] for a in observed[:-1]))
    unchanged = bool(len(observed) >= 2 and fingerprint(observed[-1]) == fingerprint(observed[-2]))
    if observed:
        best = max(observed, key=lambda a: (quality(a), a["candidate"]))
        mode = "repair"
        if quality(observed[-1]) < quality(best):
            mode, source = "restore_best", best["code"]
        elif duplicate or unchanged:
            mode, source = "rebuild", ""
    return {"task": task, "mode": mode, "current_code": source,
        "base_attempt_id": best["id"] if mode == "restore_best" else observed[-1]["id"] if observed else None,
        "current_task_feedback": [feedback_view(a) for a in observed[-2:]],
        "feedback_assessment": {"duplicate_source": duplicate, "same_failure_after_change": unchanged,
            "latest_stage_regressed": bool(observed and quality(observed[-1]) < quality(best)),
            "meaning": "Changed explanation alone is not improvement. Judge stage results and failed assertions."},
        "retrieved_knowledge": offered, "response_format_error": response_error,
        "knowledge_warning": "Stage-scoped historical observations, not proofs. Check conditions and exceptions."}


def transitions(attempts):
    """Only an executed fail -> executed pass of the SAME stage and task qualifies."""
    catalog = {}
    for stage in ("compile", "runtime"):
        failed = None
        for after in attempts:
            check = stages(after).get(stage)
            if not check or not check.get("evidence", {}).get("executed"):
                continue
            if check["status"] == "fail":
                failed = after
            elif check["status"] == "pass" and failed is not None:
                before, failed = failed, None
                if before["task_id"] != after["task_id"] or before["code_hash"] == after["code_hash"]:
                    continue
                if stage == "runtime" and quality(after)[0] != 1:
                    continue
                diff = "\n".join(difflib.unified_diff(before["code"].splitlines(), after["code"].splitlines(),
                    fromfile=before["id"], tofile=after["id"], n=4))
                # Do not let a missing part of the change support an invented rule.
                if len(diff) > 20000:
                    continue
                tid = f"{before['id']}->{after['id']}:{stage}"
                catalog[tid] = {"id": tid, "stage": stage, "before": feedback_view(before),
                    "after": feedback_view(after), "code_diff": diff,
                    "causal_status": "observed transition; changed features may be confounded"}
    return dict(list(catalog.items())[-3:])


def _validate_claim(item, catalog):
    keys = {"transition_id", "text", "applies_when", "does_not_apply_when", "checks", "keywords"}
    if not isinstance(item, dict) or set(item) != keys or item.get("transition_id") not in catalog:
        raise ProtocolError("claim schema or transition ID invalid")
    if not isinstance(item["text"], str) or not 1 <= len(item["text"].strip()) <= 800:
        raise ProtocolError("claim text exceeds bound")
    for k in keys - {"transition_id", "text"}:
        values = item[k]
        if not isinstance(values, list) or not 1 <= len(values) <= 4 or any(
                not isinstance(v, str) or not 1 <= len(v.strip()) <= 240 for v in values):
            raise ProtocolError("claim condition list invalid")
    return {**copy.deepcopy(item), "proposal_id": "p-" + object_hash(item)[:16]}


def learn(ctx, attempts, snapshot, order, scope):
    output = {"updates": [], "decisions": [], "status": "disabled", "error": None}
    if not ctx.config["method"]["learn_cross_task_knowledge"]:
        return output
    catalog = transitions(attempts)
    payload = {"current_task_id": ctx.task["id"], "task_order": order, "task": ctx.task,
               "scope": scope, "transitions": list(catalog.values())}
    ctx.write_artifact("learning/evidence_context.json", payload)
    if not catalog:
        return {**output, "status": "no_verified_stage_transition"}
    try:
        raw = ctx.ask("knowledge.curate", CURATE, payload)
        if ctx.redactor.clean(raw) != raw:
            raise ProtocolError("curation contains credential")
        if not isinstance(raw.get("claims"), list) or len(raw["claims"]) > 3:
            raise ProtocolError("invalid claims envelope")
        proposals = []
        for item in raw["claims"]:
            try:
                proposal = _validate_claim(item, catalog)
                if proposal not in proposals:
                    proposals.append(proposal)
            except (ProtocolError, TypeError) as exc:
                output["decisions"].append({"status": "rejected", "reason": str(exc)})
        ctx.write_artifact("learning/proposals.json", proposals)
        if not proposals:
            return {**output, "status": "no_admissible_claims"}
        raw = ctx.ask("knowledge.review", REVIEW, {**payload, "proposals": proposals})
        if ctx.redactor.clean(raw) != raw or not isinstance(raw.get("reviews"), list):
            raise ProtocolError("invalid review envelope")
        reviews = raw["reviews"]
        records = {a["id"]: a for a in attempts}
        for p in proposals:
            matching = [r for r in reviews if isinstance(r, dict) and r.get("proposal_id") == p["proposal_id"]]
            review = matching[0] if len(matching) == 1 else {}
            valid = (set(review) == {"proposal_id", "verdict", "reason"}
                and review.get("verdict") == "supported" and isinstance(review.get("reason"), str)
                and 1 <= len(review["reason"].strip()) <= 500)
            if not valid:
                output["decisions"].append({"proposal_id": p["proposal_id"], "status": "rejected",
                    "reason": review.get("reason", "missing, duplicate, invalid or unsupported review")})
                continue
            transition = catalog[p["transition_id"]]
            aids = [transition[k]["attempt_id"] for k in ("before", "after")]
            # Resolve evidence in code. The model never writes a quotation.
            evidence = [{"attempt_id": aid, "field": "observation", "quote": records[aid]["observation"][:1500]}
                        for aid in aids]
            stage = transition["stage"]
            stable = {k: p[k] for k in ("text", "applies_when", "does_not_apply_when", "checks", "keywords")}
            cid = "k-" + object_hash({"scope": scope, "stage": stage, **stable})[:20]
            parent = next((r for r in snapshot["records"] if r["id"] == cid), None)
            support = sorted(set((parent or {}).get("support_task_ids", []) + [ctx.task["id"]]))
            record = {**stable, "id": cid, "version": (parent or {}).get("version", 0)+1,
                "status": "active" if len(support) >= ctx.config["method"]["min_support_tasks"] else "draft",
                "scope": scope, "relation": "supports" if parent else "new", "parent_id": cid if parent else None,
                "stage": stage, "support_task_ids": support, "evidence": evidence,
                "evidence_level": f"{stage}_transition_observed_not_causal_proof",
                "transition_ids": [p["transition_id"]], "published_after_order": order, "review": review}
            if not any(r["id"] == cid for r in output["updates"]):
                output["updates"].append(record)
            output["decisions"].append({"proposal_id": p["proposal_id"], "claim_id": cid,
                                        "status": record["status"], "reason": review["reason"]})
        return {**output, "status": "stage_evidence_reviewed"}
    except (ProtocolError, ProviderError, BudgetExceeded) as exc:
        ctx.record("knowledge_learning_rejected", error_type=type(exc).__name__, message=str(exc))
        return {**output, "status": "rejected_or_budget_exhausted", "error": {"type": type(exc).__name__, "message": str(exc)}}
