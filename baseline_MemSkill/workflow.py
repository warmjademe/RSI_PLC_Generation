from baseline_common.datasets import task_query
from baseline_common.learning.protocol import EvaluationProtocol
from baseline_common.learning.runtime import make_embedder
from baseline_common.memory import resolve_root
from baseline_common.workflow import run_loop
from .learning import FrozenMemSkill


def run(task, ctx, config):
    memory = FrozenMemSkill(root=resolve_root(config), protocol=EvaluationProtocol.from_config(config),
                           embedder=make_embedder(config["encoder"]))
    def retrieve(task, feedback, ctx):
        value = memory.retrieve(task_query(task))
        return {"kind": "memskill_frozen_operations_and_memory", "content": value.text,
                "ids": value.memory_item_ids, "skill_ids": value.skill_ids}
    return run_loop(task, ctx, config, retrieve)
