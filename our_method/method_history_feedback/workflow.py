"""Current-task repair, then evidence-based publication for the next task."""
from baseline_common.context import RunContext
from baseline_common.errors import BudgetExceeded, ModelResponseError, ProtocolError, ProviderError
from baseline_common.utils import content_hash
from our_method.bound_edits import apply_revision
from our_method.validation_admission import admitted
from . import METHOD
from .config import evidence_scope
from .evidence import attempt_record, evidence_view, validate_reflection
from .knowledge import adjudicate, retrieve, validate_proposals
from . import prompts


class HistoryContext(RunContext):
    def check(self, stage, code, **kwargs):
        admission = self.config.get("validation_admission")
        if not admission:
            receipt = super().check(stage, code, **kwargs)
        else:
            with admitted(admission, self.budget.remaining_seconds()) as ticket:
                self.record("validation_admitted", stage=stage, **ticket)
                receipt = super().check(stage, code, **kwargs)
        if receipt["status"] in ("pass", "fail"):
            import copy
            import json
            directory = receipt["evidence"]["artifact_directory"]
            filename = "tool_result.json" if self.config["validators"][stage].get("result_file") else "stdout.txt"
            try:
                raw = json.loads((self.output / directory / filename).read_text(encoding="utf-8"))
                valid = (isinstance(raw, dict) and raw.get("stage") == stage
                         and all(k in raw and raw[k] == receipt[k] for k in (
                             "code_hash", "project_hash", "plan_hash", "properties_hash"))
                         and isinstance(raw.get("evidence"), dict)
                         and raw["evidence"].get("executed") is True)
            except (OSError, ValueError):
                valid = False
            if not valid:
                receipt["status"] = "error"
                receipt["diagnostics"] = ["validator must return all source/plan hashes, stage and executed=true"]
                self._receipts[receipt["check_id"]] = copy.deepcopy(receipt)
                self.write_artifact(directory + "/receipt.json", receipt)
                self.record("strict_receipt_rejected", **receipt)
        return receipt


def _generation_payload(task, code, local, reflection, offered, settings, response_error):
    if settings.get("two_level_feedback"):
        from .two_level import generation_payload
        return generation_payload(task, code, local, offered, settings, response_error)
    return {"task": task, "current_code": code, "base_code_sha256": content_hash(code),
            "current_task_history": [evidence_view(a) for a in local[-settings["max_local_attempts"]:]]
                if settings["use_task_feedback"] else [],
            "local_hypothesis": reflection if settings["use_task_feedback"] else None,
            "retrieved_knowledge": offered, "response_format_error": response_error,
            "knowledge_warning": "Conditional observations; verify applicability and validate this candidate."}


def _learning(ctx, attempts, snapshot, prior_evidence, order, scope):
    if ctx.config["method"].get("two_level_feedback"):
        from .two_level import learn
        return learn(ctx, attempts, snapshot, order, scope)
    settings = ctx.config["method"]
    output = {"updates": [], "decisions": [], "status": "disabled", "error": None}
    if not settings["learn_cross_task_knowledge"] or not attempts:
        return output
    chosen = attempts[-settings["max_curation_attempts"]:]
    first_failure = next((a for a in attempts if a["confirmed_failure"]), None)
    if first_failure and first_failure not in chosen and len(chosen) >= 2:
        chosen = [first_failure] + chosen[1:]
    available = {a["id"]: evidence_view(a) for a in chosen}
    if not any(a["checks"] for a in chosen):
        return {**output, "status": "no_tool_observations"}
    offered = retrieve(snapshot, ctx.task, scope, settings,
                       " ".join(a["observation"][:3000] for a in chosen), for_curation=True)
    parents = {r["id"]: r for r in snapshot["records"] if r["id"] in {k["id"] for k in offered}}
    # Only historical evidence for selected parents is loaded, never future tasks.
    historical_ids = {e["attempt_id"] for p in parents.values() for e in p["evidence"]}
    historical = prior_evidence(historical_ids)
    # Keep old provenance bounded. Claims whose citations are not all offered cannot be parents.
    if len(historical) > 8:
        parents = {}
        historical = {}
    available.update({aid: evidence_view(a) for aid, a in historical.items()})
    payload = {"current_task_id": ctx.task["id"], "task_order": order,
               "evidence_scope": scope, "max_claims": settings["max_new_claims"],
               "attempts": list(available.values()), "existing_claims": list(parents.values())}
    ctx.write_artifact("learning/evidence_context.json", payload)
    try:
        raw = ctx.ask("knowledge.curate", prompts.CURATE, payload)
        if ctx.redactor.clean(raw) != raw:
            raise ProtocolError("curation contains a recognizable credential")
        proposals = validate_proposals(raw, available, parents, settings["max_new_claims"])
        ctx.write_artifact("learning/proposals.json", proposals)
        if not proposals:
            return {**output, "status": "no_claims"}
        reviewed = ctx.ask("knowledge.review", prompts.REVIEW, {**payload, "proposals": proposals})
        if ctx.redactor.clean(reviewed) != reviewed:
            raise ProtocolError("review contains a recognizable credential")
        updates, decisions = adjudicate(proposals, reviewed, available, parents, scope, order,
                                        settings["min_support_tasks"])
        return {**output, "updates": updates, "decisions": decisions, "status": "reviewed"}
    except (ProtocolError, ProviderError, BudgetExceeded) as exc:
        ctx.record("knowledge_learning_rejected", error_type=type(exc).__name__, message=str(exc))
        return {**output, "status": "rejected_or_budget_exhausted", "error": {
            "type": type(exc).__name__, "message": str(exc)}}


def solve(task, config, output, *, order, snapshot, prior_evidence, provider=None, context_type=None):
    ctx = (context_type or HistoryContext)(task, config, output, method=METHOD, provider=provider)
    settings, scope = config["method"], evidence_scope(task, config)
    ctx.write_artifact("knowledge_before.json", snapshot)
    attempts, reflection, code, final_checks = [], None, ctx.latest_code, []
    status, reason, response_error = "incomplete", "candidate_budget_exhausted", ""
    offered_ids, reported_used_ids = set(), set()
    try:
        while ctx.remaining_candidates:
            candidate = ctx.begin_candidate("initial" if not attempts else "repair_or_resample")
            # A new candidate can never inherit authentic receipts from the previous candidate.
            final_checks = []
            feedback = attempts[-1]["observation"] if attempts and settings["use_task_feedback"] else ""
            offered = retrieve(snapshot, task, scope, settings, feedback)
            offered_ids.update(k["id"] for k in offered)
            ctx.write_artifact(f"attempts/{candidate:02d}/retrieved.json", offered)
            try:
                from .two_level import GENERATE as TWO_LEVEL_GENERATE
                raw = ctx.ask("plc.generate", TWO_LEVEL_GENERATE if settings.get("two_level_feedback") else prompts.GENERATE,
                              _generation_payload(task, code, attempts, reflection, offered, settings, response_error))
                if settings.get("two_level_feedback"):
                    from .two_level import apply_complete
                    code_next, edit_info = apply_complete(raw)
                else:
                    code_next, edit_info = apply_revision(code, raw)
                used = raw.get("used_knowledge_ids", [])
                if (not isinstance(used, list) or any(not isinstance(i, str) for i in used)
                        or set(used) - {k["id"] for k in offered}):
                    raise ModelResponseError("used_knowledge_ids must refer only to offered knowledge")
                summary = raw.get("change_summary", "")
                notes = raw.get("applicability_notes", "")
                if any(not isinstance(v, str) or len(v) > 4000 for v in (summary, notes)):
                    raise ModelResponseError("generation notes must be bounded strings")
                ctx.checkpoint(code_next)
                code = code_next
                response_error = ""
                reported_used_ids.update(used)
                ctx.write_artifact(f"attempts/{candidate:02d}/decision.json", {
                    **edit_info, "change_summary": summary, "applicability_notes": notes,
                    "offered_ids": [k["id"] for k in offered], "model_reported_used_ids": used})
            except ModelResponseError as exc:
                response_error = str(exc)
                attempt = attempt_record(task, order, candidate, code, [], response_error=response_error,
                                         required_stages=config["validation_stages"])
                attempts.append(attempt)
                ctx.write_artifact(f"attempts/{candidate:02d}/record.json", attempt)
                status, reason = "incomplete", "invalid_generation_response"
                continue
            all_checks = []
            for stage in config["validation_stages"]:
                receipt = None
                for retry in range(settings["unknown_tool_retries"] + 1):
                    receipt = ctx.check(stage, code)
                    all_checks.append(receipt)
                    if receipt["status"] not in ("unknown", "error"):
                        break
                    if retry < settings["unknown_tool_retries"]:
                        ctx.record("retry_uncertain_tool", stage=stage, code_hash=content_hash(code))
                final_checks.append(receipt)
                if receipt["status"] != "pass":
                    break
            attempt = attempt_record(task, order, candidate, code, all_checks, change_summary=summary,
                                     required_stages=config["validation_stages"])
            attempts.append(attempt)
            ctx.write_artifact(f"attempts/{candidate:02d}/record.json", attempt)
            reflection = None
            if attempt["passed"]:
                status, reason = "passed", "all_required_stages_passed"
                break
            last_status = final_checks[-1]["status"]
            if last_status in ("unknown", "error"):
                status, reason = "incomplete", "validator_uncertain_after_bounded_retry"
                break
            status, reason = "failed", "candidate_budget_exhausted_after_observed_failure"
            if ctx.remaining_candidates and settings["reflect_after_attempt"]:
                available = {a["id"]: evidence_view(a) for a in attempts[-settings["max_local_attempts"]:]}
                try:
                    raw = ctx.ask("task.reflect", prompts.REFLECT,
                                  {"latest_attempt_id": attempt["id"], "attempts": list(available.values())})
                    reflection = validate_reflection(raw, attempt, available)
                    ctx.write_artifact(f"attempts/{candidate:02d}/reflection.json", reflection)
                except (ProtocolError, ProviderError) as exc:
                    ctx.record("local_reflection_rejected", error_type=type(exc).__name__, message=str(exc))
    except (BudgetExceeded, ProviderError, ProtocolError) as exc:
        status, reason = "incomplete", type(exc).__name__ + ": " + str(exc)
        ctx.record("task_stopped", error_type=type(exc).__name__, message=str(exc))
        # Preserve partial stage evidence even when a later tool/call exhausts budget.
        if ctx.budget.candidates and not any(a["candidate"] == ctx.budget.candidates for a in attempts):
            receipts = [r for r in ctx._receipts.values() if r["candidate_id"] == ctx.budget.candidates
                        and r["code_hash"] == content_hash(code)]
            attempt = attempt_record(task, order, ctx.budget.candidates, code, receipts, response_error=reason,
                                     required_stages=config["validation_stages"])
            attempts.append(attempt)
            ctx.write_artifact(f"attempts/{ctx.budget.candidates:02d}/record.json", attempt)
            final_checks = list({r["stage"]: r for r in receipts}.values())
    learning = _learning(ctx, attempts, snapshot, prior_evidence, order, scope)
    publication = {"order": order, "task_id": task["id"], "knowledge_before_sha256": snapshot["sha256"],
                   "attempts": attempts, "updates": learning["updates"], "learning": learning}
    # Saved before result: recovery can publish a finished task without new paid calls.
    ctx.write_artifact("publication.json", publication)
    from baseline_common.utils import object_hash
    result = ctx.finish(code, status, reason, checks=final_checks, details={
        "protocol": "online_task_boundary_knowledge_learning", "preloaded_history_records": 0,
        "task_order": order, "knowledge_snapshot_sha256": snapshot["sha256"],
        "publication_sha256": object_hash(ctx.redactor.clean(publication)),
        "knowledge_offered_ids": sorted(offered_ids), "model_reported_used_ids": sorted(reported_used_ids),
        "knowledge_updates": len(learning["updates"]), "learning_status": learning["status"],
        "first_candidate_passed": bool(attempts and attempts[0]["candidate"] == 1 and attempts[0]["passed"]),
        "generalization_claim": "Within-stream adaptation only; no held-out generalization claim."})
    return result, ctx.redactor.clean(publication)
