from baseline_common.workflow import run_loop


def run(task, ctx, config):
    return run_loop(task, ctx, config, lambda task, feedback, ctx: {"kind": "no_cross_task_memory", "items": []})
