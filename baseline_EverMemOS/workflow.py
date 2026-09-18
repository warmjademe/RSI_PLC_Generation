from baseline_common.datasets import task_query
from baseline_common.learning.protocol import EvaluationProtocol
from baseline_common.learning.provider import LearningProvider
from baseline_common.learning.runtime import make_embedder
from baseline_common.memory import resolve_root
from baseline_common.workflow import run_loop
from .learning import FrozenEverMemOS


def run(task, ctx, config):
    memory = FrozenEverMemOS(root=resolve_root(config), protocol=EvaluationProtocol.from_config(config),
                            embedder=make_embedder(config["encoder"]), provider=LearningProvider(ctx))
    def retrieve(task, feedback, ctx):
        value = memory.retrieve(task_query(task))
        return {"kind": "evermemos_memcell_scene", "content": value.text, "ids": value.memory_item_ids,
                "retrieval_usage": value.retrieval_usage}
    return run_loop(task, ctx, config, retrieve)
