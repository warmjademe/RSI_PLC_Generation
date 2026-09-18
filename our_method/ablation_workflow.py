"""Controlled visibility interventions; private checking and stopping stay shared."""
import copy

from baseline_common import RunContext
from baseline_common.errors import ProtocolError
from .guarded_workflow import run as guarded_run


ARMS = {
    'Full': {'assets': True, 'feedback': True},
    'NoAssets': {'assets': False, 'feedback': True},
    'NoFeedback': {'assets': True, 'feedback': False},
    'Neither': {'assets': False, 'feedback': False},
}

COMMON_SYSTEM = (
    'Generate IEC 61131-3 Structured Text satisfying the public requirements. '
    'Preserve the fixed interface, scan semantics and all required behavior. '
    'The previous_code, when present, is your earlier candidate. '
    'Use supplied validation feedback only for claims supported by that feedback. '
    'An unknown result establishes neither a behavioral error nor full correctness. '
    'Training memories are fallible implementation references, not evidence of current correctness. '
    'Return one JSON object with change_summary and base_code_sha256, plus exactly one of: '
    'the complete code string, or edits containing {old,new} string replacements. '
    'Copy base_code_sha256 exactly. Every old string must match exactly once in previous_code; '
    'edits refer simultaneously to that original version and must not overlap. '
    'When previous_code is empty, return complete code. Return no Markdown fences.'
)

# Kept as an audit API alias. Every arm uses exactly the same generic system.
NO_FEEDBACK_SYSTEM = COMMON_SYSTEM


def arm_config(config, arm):
    if arm not in ARMS:
        raise ProtocolError('unknown ablation arm')
    result = copy.deepcopy(config)
    switches = ARMS[arm]
    result.update(use_code_memory=switches['assets'], use_repair_memory=switches['assets'],
                  use_skill_memory=False, use_current_task_feedback=switches['feedback'],
                  use_task_local_history=True, use_task_draft=False,
                  complete_code_after_invalid_edits=False, response_representation='bound_exact_edits')
    result['ablation_arm'] = arm
    return result


def visible_request(arm, system, payload):
    switches = ARMS[arm]
    if not switches['assets'] and payload.get('memory', {}).get('items'):
        raise ProtocolError('training asset reached an asset-disabled model boundary')
    # No generic-prompt intervention: all four arms share the same system.
    # Before an actual observation, feedback-enabled and disabled requests
    # must be byte-equivalent for the same asset setting and public task.
    result = {name: copy.deepcopy(payload[name]) for name in
              ('task', 'fixed_interface_st', 'previous_code', 'base_code_sha256', 'memory')}
    observed = bool(payload.get('confirmed_errors') or payload.get('verification_uncertainty')
                    or payload.get('task_local_attempts', {}).get('items')
                    or payload.get('repeated_rejected_outputs'))
    if switches['feedback'] and observed:
        result = copy.deepcopy(payload)
    return COMMON_SYSTEM, result


class AblationContext(RunContext):
    def __init__(self, *args, arm, **kwargs):
        if arm not in ARMS:
            raise ProtocolError('unknown ablation arm')
        self.arm = arm
        super().__init__(*args, **kwargs)

    def ask(self, role, system, payload):
        if role != 'plc.generate':
            raise ProtocolError('ablation permits only budgeted PLC generation calls')
        system, payload = visible_request(self.arm, system, payload)
        return super().ask(role, system, payload)


def run(task, ctx, config):
    if any(getattr(ctx, name, None) for name in
           ('resume_previous_code', 'resume_feedback', 'resume_task_history', 'resume_unverified_task_draft')):
        raise ProtocolError('ablation must start from scratch without previous study feedback')
    configured = arm_config(config, ctx.arm)
    if any(config.get(k) != configured[k] for k in
           ('use_code_memory', 'use_repair_memory', 'use_current_task_feedback', 'ablation_arm')):
        raise ProtocolError('frozen ablation configuration disagrees with arm identity')
    ctx.record('ablation_started', arm=ctx.arm, **ARMS[ctx.arm],
               inherited_test_attempts=0, training_asset_updates=False)
    return guarded_run(task, ctx, configured)
