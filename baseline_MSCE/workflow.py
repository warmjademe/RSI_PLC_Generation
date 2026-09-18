from dataclasses import asdict
from pathlib import Path
import contextlib
import sqlite3
from baseline_common.memory import packet, resolve_root
from baseline_common.workflow import run_loop
from .engine.models import Task, with_evaluation_scope
from .engine.retrieval import Retriever
from .engine.storage import MemoryStore


class ReadOnlyMemoryStore(MemoryStore):
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextlib.contextmanager
    def connect(self):
        con = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()


def run(task, ctx, config):
    store = ReadOnlyMemoryStore(resolve_root(config) / "memory.sqlite3")
    retriever = Retriever(store, config.get("retrieval", {}))
    def retrieve(task, feedback, ctx):
        query = with_evaluation_scope(Task(task["id"], "test", Path("."), None, task["requirement"],
                                          task.get("interface_st", ""), task.get("metadata", {})),
                                      target=task["target"], output_language="st")
        result = asdict(retriever.retrieve(query))
        items = [{"level": key, "value": item} for key in ["skills", "policies", "cognition", "traces"] for item in result[key]]
        return packet(items, int(config.get("memory_characters", 24000)), label="msce_l1_l2_l3_and_admitted_skills")
    return run_loop(task, ctx, config, retrieve)
