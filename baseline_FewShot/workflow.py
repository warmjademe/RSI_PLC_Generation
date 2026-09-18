from baseline_common.memory import code_view, compatible, load, packet, resolve_root
from baseline_common.workflow import run_loop


def run(task, ctx, config):
    _, bank = load(resolve_root(config), "FewShot")
    # No query-dependent ranking; membership and ordering stay fixed.
    return run_loop(task, ctx, config, lambda task, feedback, ctx:
                    packet([code_view(r) for r in compatible(bank, task)], int(config.get("memory_characters", 24000)), label="fixed_training_examples"))
