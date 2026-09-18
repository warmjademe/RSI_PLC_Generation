from dataclasses import asdict, replace
from pathlib import Path
import importlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

from baseline_common import ProtocolError, RunContext
from baseline_common.binding import seal_adapter, verify_adapter
from baseline_common.datasets import episode, load_public_task
from baseline_common.learning.models import AttemptStep
from baseline_common.learning.protocol import EvaluationProtocol
from baseline_common.learning.provider import ModelCall
from baseline_common.memory import load, ranked
from baseline_common.registry import METHODS


ROOT = Path(__file__).resolve().parents[1]


def sample_corpus():
    examples, episodes = [], []
    for i, noun in enumerate(["motor start stop", "temperature hysteresis", "counter pulse"], 1):
        tid = f"TR_DEMO_{i}"
        meta = {"id": tid, "split": "train", "category_id": "C01", "category": noun,
                "semantic_signature": f"sig{i}", "contamination_group_id": f"group{i}",
                "interface": {"inputs": [{"name": "Start", "type": "BOOL"}], "outputs": [{"name": "Run", "type": "BOOL"}]}}
        public = {"task_id": tid, "target": "DVP48ES300R", "metadata": meta,
                  "requirement": f"Subsystem A shall satisfy: {noun}.\nSubsystem B shall satisfy: safe output.", "interface": "Start: BOOL; Run: BOOL;"}
        code = f"FUNCTION_BLOCK {tid}\nEND_FUNCTION_BLOCK\n"
        examples.append({"id": tid, "task_id": tid, "target": public["target"], "metadata": meta,
                         "output_language": "st", "requirement": public["requirement"], "interface": public["interface"],
                         "code": code, "candidate_sha256": f"demo{i}"})
        before = AttemptStep(1, "generate", "", "BROKEN", None, "", ({"name": "compiler", "status": "fail"},), {})
        after = AttemptStep(2, "repair", "fix syntax", code, None, "", ({"name": "compiler", "status": "pass"},), {})
        episodes.append(episode(public, [before, after], key=f"run{i}", success=i != 2,
                                status="verified_success" if i != 2 else "candidate_budget_exhausted",
                                provenance={"kind": "recorded_trajectory", "completed_at": str(i)}))
    info = {"split": "train", "languages": ["st"], "task_count": 3, "test_split_accessed": False}
    return info, examples, episodes, episodes


def public_task(requirement="motor start stop"):
    return {"id": "TE_DEMO", "target": "DVP48ES300R", "requirement": requirement,
            "interface": {"inputs": {"Start": "BOOL"}, "outputs": {"Run": "BOOL"}},
            "interface_st": "Start: BOOL; Run: BOOL;", "metadata": {}, "files": {},
            "entry_file": "candidate.st", "editable_files": ["candidate.st"], "public_properties": []}


class FixtureEncoder:
    model_name = "test-only-scripted-encoder"
    revision = "fixture-v1"
    def encode(self, texts):
        return [[1.0 + text.count("motor"), 1.0 + text.count("temperature"), 1.0 + text.count("counter")] for text in texts]


class FixtureLearningProvider:
    def json_chat(self, messages, **kwargs):
        doc = {"episode": "Observed PLC training episode", "atomic_facts": ["motor needs safe stop"], "foresight": []}
        call = ModelCall(json.dumps(doc), "fixture", "fixture", {"input_tokens": 1, "output_tokens": 1}, 0.0, "test-only")
        return doc, call


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.corpus = sample_corpus()

    def tearDown(self):
        self.tmp.cleanup()

    def fit(self, method, settings=None):
        root = self.root / method
        settings = settings or {}
        module = importlib.import_module(METHODS[method] + ".training")
        summary = module.train(self.corpus, root, settings)
        seal_adapter(root, method, self.corpus, settings, summary)
        return root

    def run_method(self, method, memory=None, *, fail=False, unknown=False, calls=5):
        tool = str(ROOT / "tests/fixture_validator.py")
        validators = {} if unknown else {s: {"kind": "command", "command": [sys.executable, tool, "fail" if fail else "pass"], "protocol": "json", "test_only": True}
                                        for s in ["compile", "specification"]}
        settings = {"memory_root": str(memory)} if memory else {}
        conf = {"provider": {"kind": "replay", "responses": [
            {"role": "plc.generate", "response": {"code": f"FUNCTION_BLOCK TE_DEMO\n(* revision {i} *)\nEND_FUNCTION_BLOCK"}}
            for i in range(calls)]}, "validators": validators, "method": settings}
        ctx = RunContext(public_task(), conf, self.root / (method + "-run"), method=method)
        result = importlib.import_module(METHODS[method] + ".workflow").run(public_task(), ctx, settings)
        return result, ctx

    def test_vanilla_has_no_training_context(self):
        result, ctx = self.run_method("Vanilla")
        self.assertEqual(result["status"], "passed")
        request = json.loads((ctx.output / "model/0001/request.json").read_text())
        self.assertEqual(request["payload"]["memory"]["items"], [])

    def test_fixed_fewshot_is_frozen_before_query(self):
        root = self.fit("FewShot", {"shots": 2, "seed": 7})
        _, records = load(root, "FewShot")
        self.assertEqual(len(records), 2)
        before = (root / "records.jsonl.gz").read_bytes()
        result, _ = self.run_method("FewShot", root)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(before, (root / "records.jsonl.gz").read_bytes())

    def test_final_code_retrieval_changes_with_query(self):
        root = self.fit("FinalCodeRAG")
        _, records = load(root, "FinalCodeRAG")
        self.assertEqual(ranked(records, public_task("temperature hysteresis"), 1)[0]["id"], "TR_DEMO_2")
        self.assertEqual(ranked(records, public_task("motor start stop"), 1)[0]["id"], "TR_DEMO_1")
        result, _ = self.run_method("FinalCodeRAG", root)
        self.assertEqual(result["status"], "passed")

    def test_raw_trajectory_preserves_failed_outcome(self):
        root = self.fit("RawTrajectoryRAG")
        _, records = load(root, "RawTrajectoryRAG")
        failed = next(r for r in records if r["id"] == "TR_DEMO_2")
        self.assertFalse(failed["success"])
        self.assertEqual(failed["reward"], 0.0)
        self.assertEqual(failed["attempts"][0]["feedback"][0]["status"], "fail")
        result, _ = self.run_method("RawTrajectoryRAG", root)
        self.assertEqual(result["status"], "passed")

    def test_our_method_only_uses_repairs_from_successful_episodes(self):
        from our_method.retrieval import retrieve
        root = self.fit("OurMethod")
        _, records = load(root, "OurMethod")
        repairs = [r for r in records if r["kind"] == "successful_trajectory_repair"]
        self.assertEqual({r["task_id"] for r in repairs}, {"TR_DEMO_1", "TR_DEMO_3"})
        first = retrieve(records, public_task(), [], {})
        self.assertFalse(any(r["kind"] == "successful_trajectory_repair" for r in first["items"]))
        repair = retrieve(records, public_task(), [{"stage": "compile", "status": "fail"}], {})
        self.assertTrue(any(r["kind"] == "successful_trajectory_repair" for r in repair["items"]))
        result, _ = self.run_method("OurMethod", root)
        self.assertEqual(result["status"], "passed")

    def test_our_method_oversized_bank_does_not_rescan_all_contracts_per_rejection(self):
        from our_method import retrieval
        bank = [{**self.corpus[1][0], "id": f"large_{i}", "kind": "verified_program", "code": "X" * 30000} for i in range(100)]
        with patch.object(retrieval, "clauses", wraps=retrieval.clauses) as parse:
            result = retrieval.retrieve(bank, public_task(), [], {})
        self.assertEqual(result["items"], [])
        self.assertLess(parse.call_count, 300)

    def test_no_sixth_candidate_after_five_failures(self):
        result, ctx = self.run_method("Vanilla", fail=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(ctx.budget.model_calls, 5)
        self.assertEqual(ctx.budget.candidates, 5)

    def test_missing_tool_does_not_trigger_code_repair(self):
        result, ctx = self.run_method("Vanilla", unknown=True)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(ctx.budget.model_calls, 1)
        self.assertEqual(ctx.budget.tool_calls, 2)

    def test_memory_tampering_and_query_leakage_are_rejected(self):
        root = self.fit("FinalCodeRAG")
        with self.assertRaises(ProtocolError):
            verify_adapter(root, "FinalCodeRAG", {**public_task(), "id": "TR_DEMO_1"})
        with self.assertRaises(ProtocolError):
            verify_adapter(root, "FinalCodeRAG", {**public_task(), "metadata": {"contamination_group_id": "group1"}})
        (root / "records.jsonl.gz").write_bytes(b"tampered")
        with self.assertRaises(ProtocolError):
            verify_adapter(root, "FinalCodeRAG")

    def test_memento_actual_train_freeze_retrieve(self):
        from baseline_Memento.learning import FrozenMemento, MementoTrainer
        from baseline_common.datasets import task_query
        protocol = EvaluationProtocol.from_config({})
        episodes = [replace(self.corpus[2][i % 3], task_id=f"TR_{i}", run_key=f"run{i}") for i in range(1000)]
        root = self.root / "Memento"
        trainer = MementoTrainer(protocol=protocol, embedder=FixtureEncoder())
        manifest = trainer.train_and_freeze(episodes, output_root=root, corpus_binding={}, source_revision="test")
        self.assertEqual(manifest["case_count"], 1000)
        memory = FrozenMemento(root=root, protocol=protocol, embedder=FixtureEncoder())
        result = memory.retrieve(task_query(public_task()))
        self.assertLessEqual(len(result.memory_item_ids), 4)
        self.assertIn("past_reward", result.text)

    def test_evermemos_forms_cells_and_scenes(self):
        from baseline_EverMemOS.learning import EverMemOSTrainer, FrozenEverMemOS
        from baseline_common.datasets import task_query
        protocol = EvaluationProtocol.from_config({})
        root = self.root / "EverMemOS"
        trainer = EverMemOSTrainer(protocol=protocol, embedder=FixtureEncoder(), provider=FixtureLearningProvider())
        manifest = trainer.train_and_freeze(self.corpus[2], output_root=root, corpus_binding={}, source_revision="test", expected_count=3)
        self.assertEqual(manifest["cell_count"], 3)
        self.assertEqual(manifest["model_call_count"], 6)
        memory = FrozenEverMemOS(root=root, protocol=protocol, embedder=FixtureEncoder(), provider=None)
        self.assertTrue(memory.retrieve(task_query(public_task())).memory_item_ids)

    def test_memskill_rejects_unretrieved_update_and_preserves_history(self):
        from baseline_MemSkill.learning import MemSkillTrainer, _initial_operations
        operations = list(_initial_operations().values())
        insert = next(o for o in operations if o.update_type == "insert")
        update = next(o for o in operations if o.update_type == "update")
        self.assertEqual(MemSkillTrainer._parse_actions({"actions": [{"operation": update.name, "memory_id": "missing", "content": "invented"}]}, selected=[update], retrieved=[]), [])
        actions = MemSkillTrainer._parse_actions({"actions": [{"operation": insert.name, "content": "verified trigger"}]}, selected=[insert], retrieved=[])
        memories = []
        MemSkillTrainer._apply_actions(actions, episode=self.corpus[2][0], memories=memories, step=1)
        self.assertEqual(len(memories), 1)
        actions = MemSkillTrainer._parse_actions({"actions": [{"operation": update.name, "memory_id": memories[0].memory_id, "content": "updated trigger"}]}, selected=[update], retrieved=memories)
        MemSkillTrainer._apply_actions(actions, episode=self.corpus[2][0], memories=memories, step=2)
        self.assertEqual(memories[0].content_history, ["verified trigger"])

    def test_msce_evidence_validation_and_value_backfill(self):
        from baseline_MSCE.engine.induction import backfill_values, validate_policy, InductionError
        self.assertEqual(backfill_values(1, 1.0), [1.0])
        with self.assertRaises(InductionError):
            validate_policy({"trigger": "T", "procedure": ["P"], "verification": ["V"], "boundary": ["B"],
                             "evidence_episode_ids": [123, 124], "confidence": 0.8}, {1, 2})

    def test_msce_training_does_not_invent_skill_gain(self):
        from baseline_MSCE.training import train
        from baseline_MSCE.workflow import ReadOnlyMemoryStore
        class Provider:
            def json_chat(self, messages, **kwargs):
                doc = {"summary": "S", "causal_diagnosis": "unproven", "reusable_lesson": "recheck",
                       "boundary": ["training only"], "scores": {k: .5 for k in ["correctness", "specificity", "evidence_grounding", "reusability"]},
                       "trigger": "PLC", "procedure": ["check"], "verification": ["compile"],
                       "evidence_episode_ids": [1, 3], "confidence": .5}
                return doc, ModelCall(json.dumps(doc), "fixture", "fixture", {}, 0.0, "test-only")
        root = self.root / "MSCE"
        report = train(self.corpus, root, {}, provider=Provider())
        self.assertEqual(report["counts"]["policies"], 1)
        self.assertEqual(report["counts"]["skills"], 0)
        with ReadOnlyMemoryStore(root / "memory.sqlite3").connect() as con:
            with self.assertRaises(Exception):
                con.execute("DELETE FROM episodes")

    def test_public_loader_never_reads_evaluator(self):
        test_root = ROOT / "test_dataset"
        ids = json.loads((test_root / "selection.json").read_text())["selected_task_ids"]
        original = Path.read_text
        def safe_read(path, *args, **kwargs):
            self.assertNotIn("evaluator", path.parts)
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", safe_read):
            task = load_public_task(test_root, ids[0])
        self.assertEqual(task["language"], "st")
        self.assertNotIn("reference.st", json.dumps(task))


if __name__ == "__main__":
    unittest.main()
