"""Task-local repair with failure-focused instructions and duplicate detection.

This is an exploratory revision, not a claim of RSI promotion. Only frozen
training records are retrieved. Current-task attempts never become assets.
"""
import json
import math
import re
from baseline_common.binding import verify_adapter
from baseline_common.memory import load, resolve_root
from baseline_common.errors import ModelResponseError
from baseline_common.utils import content_hash
from .guarded_retrieval import retrieve
from .bound_edits import apply_revision
from .repair_history import validate_history, history_packet, history_item
from .task_draft import draft_packet

SYSTEM = (
    'You are repairing IEC 61131-3 Structured Text against authoritative compiler, '
    'runtime and formal diagnostics. Preserve every required POU and public interface. '
    'The previous program has NOT passed the checks. Return a JSON object containing '
    'a short change_summary and the complete corrected code string. '
    'Fix the concrete failing expression or state transition; copying the previous '
    'program verbatim is not a repair. Keep code comments short. Do not change the '
    'requirements or explain away a failing test. Training snippets are conditional '
    'references, not validated solutions of the current task. '
    'Return only the requested JSON; no prose outside it.'
)


def focus_feedback(feedback, code):
    lines = code.splitlines()
    focused = []
    for item in feedback:
        if item.get('status') != 'fail':
            continue
        diagnostics = item.get('diagnostics', [])
        rendered = json.dumps(diagnostics, ensure_ascii=False)
        locations = sorted({int(n) for n in re.findall(r'candidate\.st:(\d+)', rendered)})
        excerpts = []
        for n in locations[:10]:
            excerpts.append({'line': n, 'source': '\n'.join(
                f'{j + 1}: {lines[j]}' for j in range(max(0, n - 3), min(len(lines), n + 2)))})
        focused.append({'stage': item.get('stage'), 'status': 'fail',
                        'diagnostics': diagnostics, 'source_locations': excerpts})
    return focused


def repairable_frontend(check):
    return check.get('status') in ('unknown', 'error') and any(
        isinstance(d, dict) and d.get('kind') == 'verification_frontend_error' and d.get('repairable_input') is True
        for d in check.get('diagnostics', []))


def repairable_runtime_termination(check):
    """A reported arithmetic termination can guide repair; its verdict stays unknown."""
    return check.get('stage') == 'runtime' and check.get('status') == 'unknown' and any(
        isinstance(d, str) and d.strip() == 'native runtime exited -8'
        for d in check.get('diagnostics', []))


def refactorable_formal_timeout(checks):
    """Select only witnessed solver timeouts after both prerequisite stages passed.

    This is permission to propose a different candidate, never a failure or a
    proof of equivalence. An inconclusive portfolio needs a bound timeout
    witness; its other diagnostics remain unknown, not explained away.
    """
    latest = {c.get('stage'): c for c in checks}
    if any(latest.get(stage, {}).get('status') != 'pass' for stage in ('compile', 'runtime')):
        return False
    check = latest.get('formal', {})
    diagnostics = check.get('diagnostics', [])
    def witnessed(d):
        if (not isinstance(d, dict) or d.get('status') != 'unknown'
                or type(d.get('property_index')) is not int or d['property_index'] <= 0):
            return False
        if d.get('reason') == 'PLCverif/backend exceeded its allocated property time.':
            return True
        evidence = d.get('backend_timeout_evidence', {})
        digest = evidence.get('code_hash')
        if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or check.get('code_hash', digest) != digest
                or evidence.get('property_index') != d['property_index']):
            return False
        witnesses = evidence.get('witnesses', [])
        return bool(witnesses) and all(
            isinstance(w, dict) and w.get('source') in ('bound_tool_result_backend_attempt', 'manifest_bound_scan_completion')
            and w.get('status') == 'unknown' and w.get('executed') is True and w.get('timed_out') is True
            and all(type(w.get(k)) in (int, float) and math.isfinite(w[k]) and w[k] > 0
                    for k in ('allocated_seconds', 'elapsed_seconds'))
            for w in witnesses)
    return check.get('status') == 'unknown' and bool(diagnostics) and all(witnessed(d) for d in diagnostics)


def run(task, ctx, config):
    root = resolve_root(config)
    verify_adapter(root, 'OurMethod', task)
    if (config.get('asset_representation') == 'historical_revision_policy_v1'
            and not config.get('use_code_memory', True) and not config.get('use_repair_memory', True)):
        bank = []
    else:
        _, bank = load(root, 'OurMethod')
    code = getattr(ctx, 'resume_previous_code', '')
    feedback = getattr(ctx, 'resume_feedback', [])
    frontend_repairs = config.get('repair_frontend_errors', False)
    runtime_repairs = config.get('repair_runtime_crashes', False)
    formal_refactors = config.get('refactor_formal_timeouts', False)
    def actionable(check):
        return check.get('status') == 'fail' or (frontend_repairs and repairable_frontend(check)) or (
            runtime_repairs and repairable_runtime_termination(check))
    edit_mode = config.get('response_representation') == 'bound_exact_edits'
    format_fallback = edit_mode and config.get('complete_code_after_invalid_edits', False)
    complete_code_only = False
    use_history = config.get('use_task_local_history', False)
    history = list(validate_history(getattr(ctx, 'resume_task_history', []), task)) if use_history else []
    seen = {}
    for item in history:
        if any(actionable(f) for f in item['feedback']):
            seen[item['code_hash']] = item['feedback']
        else:
            seen.pop(item['code_hash'], None)
    if code and any(actionable(f) for f in feedback):
        seen[content_hash(code)] = feedback
    unchanged = 0
    checks = []
    while ctx.remaining_candidates:
        timeout_refactor = formal_refactors and refactorable_formal_timeout(feedback)
        retrieval_feedback = feedback if config.get('use_current_task_feedback', True) else []
        if config.get('asset_representation') == 'historical_revision_policy_v1':
            from .repair_policy import retrieve as policy_retrieve
            memory = policy_retrieve(bank, task, retrieval_feedback, config, code)
            ctx.write_artifact(f'policy/candidate-{ctx.budget.candidates+1:02d}.json', {
                'base_code_sha256': content_hash(code), 'feedback': retrieval_feedback,
                'feedback_enabled': config.get('use_current_task_feedback', True),
                'training_asset_updates': False})
        else:
            memory = retrieve(bank, task, retrieval_feedback, config)
        ctx.begin_candidate('focused_feedback_repair' if feedback else 'initial_generation')
        # Previous-candidate receipts cannot be attached to a later malformed
        # response. Keep them in the audit, not in this candidate's delivery.
        checks = []
        strategy = (
            'Locate the exact fault indicated by the checks, implement the smallest sufficient correction, '
            'and return the whole corrected ST program. In change_summary name the changed expression or transition.'
        )
        if unchanged and config.get('use_current_task_feedback', True):
            strategy = (
                'The preceding response repeated an already failing program. Do not copy it again. '
                'Re-derive the failing part directly from the public requirement and diagnostic. '
                'Use a different valid implementation for that part, and preserve the fixed interfaces. '
                'Name the concrete change in change_summary.'
            )
            # Avoid repeatedly anchoring the retry on the same long program.
            memory['items'] = [m for m in memory['items'] if m['kind'] != 'verified_contract_donor']
            if config.get('asset_representation') != 'historical_revision_policy_v1':
                memory['selection_audit']['sent_programs'] = 0
        if frontend_repairs and any(repairable_frontend(f) for f in feedback):
            strategy = (
                'The formal frontend could not accept this candidate. Use the concrete parser diagnostics '
                'and source excerpt to make a semantically equivalent correction. Preserve the published '
                'requirements and behavior already checked by runtime tests. An unknown result does not '
                'prove a behavioral error; the revised program must still pass every check.'
            )
        if runtime_repairs and any(repairable_runtime_termination(f) for f in feedback):
            strategy = (
                'The native runtime terminated with an arithmetic signal, so behavior was not established. '
                'Inspect numeric operations, divisors, MOD operands, ranges and their scan-dependent values '
                'against the public requirements. Return a concrete correction when supported by the code '
                'and input contract. Do not infer a passing or failing behavioral verdict from the signal, '
                'change the tests, or suppress required outputs. The revision needs fresh complete checks.'
            )
        extra = {}
        if config.get('use_task_draft', False):
            draft = draft_packet(getattr(ctx, 'resume_unverified_task_draft', None), task, code)
            if draft is not None:
                extra['unverified_task_draft'] = draft
                ctx.write_artifact(f'task_draft/candidate-{ctx.budget.candidates:02d}.json', draft)
                strategy += (
                    ' An unfinished model draft for this exact program is supplied as unverified data. '
                    'It may contain wrong assumptions or incomplete proposals. Use it only when consistent '
                    'with the public requirements and actual diagnostics, which take precedence. '
                    'Return the concrete ST revision; the draft is neither verified knowledge nor a tool result.'
                )
        if use_history:
            packet = history_packet(history, task)
            ctx.write_artifact(f'task_history/candidate-{ctx.budget.candidates:02d}.json', packet)
            extra['task_local_attempts'] = packet
            strategy += (
                ' Before editing, identify the exact public requirement and the input condition or scan '
                'transition involved. In change_summary state a falsifiable explanation and the expected '
                'effect of the change. Consult the same-task attempt history to avoid previously rejected '
                'versions. If local edits repeatedly fail, a complete reimplementation of the faulty part '
                'is allowed within the same response and interface constraints.'
            )
        if timeout_refactor:
            strategy = (
                'Compilation and the configured runtime tests passed, but the formal backend exceeded '
                'its allocated property time. No behavioral error or full correctness was established. '
                'Propose a concrete, structurally simpler implementation intended to preserve behavior '
                'for all allowed inputs and scans. Inspect redundant state, repeated computations, '
                'control-flow branching and loop-carried state; simplify only when justified by the '
                'public contract and the actual program. Preserve interface, scan update order, '
                'reset priority, retained state, numeric widths/overflow and array bounds. Do not '
                'change requirements, properties, tests, timeouts or verification scope, remove '
                'required behavior, specialize to observed test inputs, or merely edit comments. '
                'In change_summary explain the concrete structural change and its preservation '
                'argument without claiming verified equivalence. Consult same-task history when '
                'supplied. Every changed candidate needs fresh compile, runtime and formal checks.'
            )
        ctx.write_artifact(f'memory/candidate-{ctx.budget.candidates:02d}.json', memory)
        system = SYSTEM if feedback else SYSTEM.replace('The previous program has NOT passed the checks. ', '')
        if timeout_refactor:
            system = system.replace(
                'Fix the concrete failing expression or state transition; copying the previous '
                'program verbatim is not a repair.',
                'The bound tool records include a formal backend timeout; other inconclusive '
                'diagnostics retain their original meanings. Propose a concrete structural '
                'refactoring intended to preserve the required behavior; do not invent a logical error.')
        if edit_mode:
            if complete_code_only:
                response_instruction = (
                    'change_summary, base_code_sha256, and the complete corrected code string. '
                    'Do not return edits. Copy the supplied base_code_sha256 exactly. '
                    'A previous edit response was rejected and those edits were not applied. '
                    'Use the supplied previous_code as the current base. A response-format rejection '
                    'does not establish a PLC behavioral error; preserve the meaning of the tool feedback.')
            else:
                response_instruction = (
                    'change_summary, base_code_sha256, and edits: a list of {old, new} string replacements. '
                    'Every old string must occur exactly once in the supplied previous_code. All edits refer '
                    'to that same original version and must not overlap. Copy its base_code_sha256 exactly. '
                    'Prefer short local edits. If a full rewrite is necessary, return code instead of edits, '
                    'still with base_code_sha256. Do not return both formats.')
            system = system.replace('a short change_summary and the complete corrected code string.', response_instruction)
        reply = None
        try:
            reply = ctx.ask('plc.generate', system, {
                'task': {k: task[k] for k in ['id', 'requirement', 'target', 'interface']},
                'fixed_interface_st': task.get('interface_st', ''),
                'repair_instruction': strategy, 'previous_code': code,
                **({'base_code_sha256': content_hash(code)} if edit_mode else {}),
                **({'required_response_representation': 'complete_code_with_base_hash'} if complete_code_only else {}),
                'confirmed_errors': focus_feedback(feedback, code), 'memory': memory,
                'verification_uncertainty': [f for f in feedback if f.get('status') in ('unknown', 'error')],
                'repeated_rejected_outputs': unchanged,
                **extra,
            })
            if edit_mode:
                if complete_code_only and 'edits' in reply:
                    raise ModelResponseError('complete code is required after an invalid edit response; do not return edits')
                if config.get('asset_representation') == 'historical_revision_policy_v1':
                    from .repair_policy import apply_action
                    candidate, operation = apply_action(code, reply, memory)
                else:
                    candidate, operation = apply_revision(code, reply)
            else:
                candidate, operation = reply.get('code'), {'representation': 'complete_code', 'edit_count': None}
            if not isinstance(candidate, str) or not candidate.strip():
                raise ModelResponseError('Missing nonempty code string')
        except ModelResponseError as exc:
            feedback = [f for f in feedback if f.get('stage') != 'response_format'] + [
                {'stage': 'response_format', 'status': 'fail', 'diagnostics': [str(exc)]}]
            ctx.record('candidate_response_rejected', reason=str(exc))
            if format_fallback and not complete_code_only and isinstance(reply, dict) and 'edits' in reply:
                complete_code_only = True
                ctx.record('response_format_fallback', code_hash=content_hash(code),
                           required_response_representation='complete_code_with_base_hash',
                           reason=str(exc), extra_model_calls=0, budget_reset=False)
            continue
        digest = content_hash(candidate)
        duplicate = digest in seen
        ctx.record('repair_change', changed_from_previous=candidate != code,
                   duplicate_of_rejected=duplicate, change_summary=reply.get('change_summary', ''), **operation)
        code = candidate
        ctx.checkpoint(code)
        if duplicate:
            unchanged += 1
            feedback = seen[digest]
            # No program change: keep confirmed feedback and avoid repeating
            # expensive validation. No success can be claimed from cached checks.
            checks = []
            continue
        checks = []
        failed = False
        for stage in ('compile', 'runtime', 'formal'):
            check = ctx.check(stage, code)
            if check['status'] in ('unknown', 'error') and not (frontend_repairs and repairable_frontend(check)):
                check = ctx.check(stage, code)
            checks.append(check)
            if frontend_repairs and repairable_frontend(check):
                feedback = [{'stage': c['stage'], 'status': c['status'],
                             'diagnostics': c['diagnostics'], 'evidence': c.get('evidence', {})} for c in checks]
                seen[digest] = feedback
                if use_history:
                    history.append(history_item(task, code, checks, origin='current_round', change_summary=reply.get('change_summary', '')))
                ctx.record('repairable_frontend_rejection', code_hash=digest, status=check['status'])
                failed = True
                break
            if runtime_repairs and repairable_runtime_termination(check):
                feedback = [{'stage':c['stage'], 'status':c['status'], 'diagnostics':c['diagnostics'],
                             'evidence':c.get('evidence',{})} for c in checks]
                seen[digest] = feedback
                if use_history:
                    history.append(history_item(task,code,checks,origin='current_round',change_summary=reply.get('change_summary','')))
                ctx.record('repairable_runtime_termination',code_hash=digest,status='unknown')
                failed = True
                break
            if formal_refactors and refactorable_formal_timeout(checks):
                feedback = [{'stage': c['stage'], 'status': c['status'], 'diagnostics': c['diagnostics'],
                             'evidence': c.get('evidence', {})} for c in checks]
                # Keep unknown outside the known-failure cache. Repeated code
                # still needs real checks; a timeout cannot supply a verdict.
                if use_history:
                    history.append(history_item(task, code, checks, origin='current_round',
                                                change_summary=reply.get('change_summary', '')))
                ctx.record('formal_timeout_refactoring', code_hash=digest, status='unknown',
                           semantic_equivalence='not_established')
                failed = True
                break
            if check['status'] in ('unknown', 'error'):
                return ctx.finish(code, 'incomplete', 'Verification did not establish a result', checks=checks,
                                  details={'learning_during_test': False})
            if check['status'] == 'fail':
                feedback = [{'stage': c['stage'], 'status': c['status'],
                             'diagnostics': c['diagnostics'], 'evidence': c.get('evidence', {})} for c in checks]
                seen[digest] = feedback
                if use_history:
                    history.append(history_item(task, code, checks, origin='current_round', change_summary=reply.get('change_summary', '')))
                failed = True
                break
        if not failed:
            return ctx.finish(code, 'passed', 'All configured checks passed for this candidate', checks=checks,
                              details={'learning_during_test': False, 'revision': config.get('workflow_revision', 'guarded_repair_v2')})
    uncertain = any(f.get('status') in ('unknown', 'error') for f in feedback) and not any(
        f.get('status') == 'fail' and f.get('stage') in ('compile', 'runtime', 'formal') for f in feedback)
    return ctx.finish(code, 'incomplete' if uncertain else 'failed',
                      f"Candidate revision budget exhausted (limit {ctx.budget.limits['max_candidates']})", checks=checks,
                      details={'learning_during_test': False, 'duplicate_rejections': unchanged})
