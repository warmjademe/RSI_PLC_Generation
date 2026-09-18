from baseline_common.memory import code_view, compatible, load, packet, ranked, resolve_root
from baseline_common.workflow import run_loop


def run(task, ctx, config):
    _, bank = load(resolve_root(config), "FinalCodeRAG")
    def retrieve(task, feedback, ctx):
        matches = ranked(compatible(bank, task), task, int(config.get("top_k", 2)))
        return packet([code_view(r) for r in matches], int(config.get("memory_characters", 24000)), label="verified_final_programs")
    return run_loop(task, ctx, config, retrieve)
