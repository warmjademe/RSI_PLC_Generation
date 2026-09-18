"""Same candidate loop, tool policy and budget for the memory comparison."""
from .errors import ProtocolError, ModelResponseError


SYSTEM = (
    "Generate one complete IEC 61131-3 Structured Text FUNCTION_BLOCK satisfying the public requirements. "
    "Preserve the fixed input/output interface. Return a JSON object with one string field 'code'. "
    "Training memories are fallible references, never evidence that the current program is correct. "
    "Only repair a confirmed program failure; do not weaken requirements or checks."
)


def run_loop(task, ctx, config, retrieve):
    if config.get("memory_root"):
        from .binding import verify_adapter
        verify_adapter(config["memory_root"], ctx.method, task)
    stages = config.get("validation_stages", ["compile", "specification"])
    if not isinstance(stages, list) or not stages or any(s not in ["compile", "formal", "specification", "runtime"] for s in stages):
        raise ProtocolError("validation_stages must be a nonempty supported stage list")
    if len(set(stages)) != len(stages):
        raise ProtocolError("Duplicate validation stage")
    retries = config.get("unknown_tool_retries", 1)
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 3:
        raise ProtocolError("unknown_tool_retries must be between 0 and 3")
    feedback = getattr(ctx, 'resume_feedback', [])
    code = getattr(ctx, 'resume_previous_code', '')
    checks = []
    while ctx.remaining_candidates or getattr(ctx, "resume_candidate_pending", False):
        if getattr(ctx, "resume_candidate_pending", False):
            # This attempt was reserved before STOP but never dispatched. Reuse its
            # original retrieved context and candidate ID, including candidate 20.
            context = ctx.resume_memory
            ctx.resume_candidate_pending = False
            ctx.record("reserved_candidate_resumed", candidate_id=ctx.budget.candidates)
        else:
            context = retrieve(task, feedback, ctx)
            ctx.write_artifact(f"memory/candidate-{ctx.budget.candidates + 1:02d}.json", context)
            ctx.begin_candidate("initial_generation" if not feedback else "feedback_repair")
        checks = []  # Do not inherit receipts across malformed candidates.
        try:
            reply = ctx.ask("plc.generate", SYSTEM, {
                "task": {k: task[k] for k in ["id", "requirement", "target", "interface"]},
                "fixed_interface_st": task.get("interface_st", ""), "memory": context,
                "previous_code": code, "feedback": feedback,
            })
            candidate = reply.get("code")
            if not isinstance(candidate, str) or not candidate.strip():
                raise ModelResponseError("Generator must return a nonempty ST code field")
        except ModelResponseError as exc:
            feedback = [{"stage": "response_format", "status": "fail", "diagnostics": [str(exc)]}]
            ctx.record("candidate_response_rejected", reason=str(exc))
            continue
        code = candidate
        ctx.checkpoint(code)
        checks, failed = [], False
        for stage in stages:
            for retry in range(retries + 1):
                check = ctx.check(stage, code)
                if check["status"] not in ["unknown", "error"]:
                    break
                ctx.record("uncertain_validation", stage=stage, retry=retry, status=check["status"])
            checks.append(check)
            if check["status"] in ["unknown", "error"]:
                return ctx.finish(code, "incomplete", "Configured validation did not establish a result", checks=checks,
                                  details={"success_scope": "none", "required_stages": stages})
            if check["status"] == "fail":
                feedback = [{"stage": c["stage"], "status": c["status"], "diagnostics": c["diagnostics"],
                             "evidence": c.get("evidence", {})} for c in checks]
                failed = True
                break
        if not failed:
            return ctx.finish(code, "passed", "All configured stages passed for the delivered candidate", checks=checks,
                              details={"success_scope": "configured_tool_checks_only", "required_stages": stages,
                                       "independent_test_score": None, "learning_during_test": False})
    return ctx.finish(code, "failed", "Candidate budget exhausted after confirmed failures", checks=checks,
                      details={"success_scope": "none", "required_stages": stages})
