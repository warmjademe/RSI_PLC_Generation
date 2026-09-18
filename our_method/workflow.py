from baseline_common.memory import load, resolve_root
from baseline_common.workflow import run_loop
from .retrieval import retrieve


def run(task, ctx, config):
    _, bank = load(resolve_root(config), "OurMethod")
    def memory(task, feedback, ctx):
        audit={}
        result=retrieve(bank,task,feedback,config,audit=audit)
        ctx.record('our_method_memory_selection',candidate=ctx.budget.candidates+1,**audit)
        return result
    return run_loop(task, ctx, config, memory)
