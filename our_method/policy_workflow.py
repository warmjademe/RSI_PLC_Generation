"""Three-arm interface for the historical revision policy experiment."""
import time
from baseline_common.context import RunContext
from baseline_common.errors import ProtocolError
from .ablation_workflow import ARMS, arm_config, visible_request
from .guarded_workflow import run as guarded_run
from .validation_admission import admitted

SYSTEM = (
    'Generate IEC 61131-3 Structured Text satisfying the public requirements. '
    'Preserve the required POU kinds, names, fixed interfaces and scan semantics. '
    'previous_code is your own earlier candidate. Diagnostics describe only the '
    'configured checks; unknown establishes neither an error nor full correctness. '
    'Historical actions are fallible references. For a proposed change, check the '
    'target requirement, reset/enable priority, initialization, state retention, '
    'numeric types and update order. Source progress is not target verification. '
    'Return one JSON object with change_summary and base_code_sha256, and exactly '
    'one of: code (complete ST), edits (a list of {old,new} replacements), or '
    'action_id (an exact executable_options action_id offered in memory). '
    'Copy the supplied base_code_sha256 exactly. An old string must occur exactly '
    'once in previous_code; simultaneous edits must not overlap. An action_id '
    'selects only the offered bound edit, not its source program. If no offered '
    'action applies, generate your own code or edits. Initially return complete '
    'code. Explain the intended behavioral effect in change_summary. Return no '
    'Markdown fences. Use IEC (* ... *) comments.'
)


class PolicyContext(RunContext):
    def __init__(self, *args, arm, **kwargs):
        if arm not in ('Full', 'NoAssets', 'NoFeedback'):
            raise ProtocolError('this protocol has exactly three arms')
        self.arm = arm
        super().__init__(*args, **kwargs)

    def check(self, stage, code, **kwargs):
        config = self.config.get('validation_admission')
        if config is None:
            return super().check(stage, code, **kwargs)
        number = self.budget.tool_calls + 1
        with admitted(config, self.budget.remaining_seconds()) as receipt:
            self.write_artifact(f'admission/tool-{number:04d}.json', {'stage': stage, **receipt})
            self.record('validation_admitted', stage=stage, **receipt)
            started = time.monotonic()
            try:
                return super().check(stage, code, **kwargs)
            finally:
                self.write_artifact(f'admission/tool-{number:04d}.json', {
                    'stage': stage, **receipt, 'execution_seconds': time.monotonic()-started})

    def ask(self, role, system, payload):
        if role != 'plc.generate':
            raise ProtocolError('only budgeted candidate generation is permitted')
        _, visible = visible_request(self.arm, system, payload)
        return super().ask(role, SYSTEM, visible)


def run(task, ctx, config):
    expected = arm_config(config, ctx.arm)
    if any(config.get(k) != expected[k] for k in
           ('use_code_memory', 'use_repair_memory', 'use_current_task_feedback', 'ablation_arm')):
        raise ProtocolError('frozen switches disagree with arm')
    if config.get('asset_representation') != 'historical_revision_policy_v1':
        raise ProtocolError('historical revision policy bank required')
    if any(getattr(ctx, k, None) for k in ('resume_previous_code', 'resume_feedback', 'resume_task_history')):
        raise ProtocolError('test tasks must start without inherited attempts')
    ctx.record('ablation_started', arm=ctx.arm, **ARMS[ctx.arm],
               inherited_test_attempts=0, training_asset_updates=False)
    return guarded_run(task, ctx, expected)
