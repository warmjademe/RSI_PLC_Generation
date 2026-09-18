import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from baseline_common.errors import ProtocolError
from baseline_common.providers import ReplayProvider
from baseline_common.utils import content_hash, object_hash
from our_method.method_history_feedback.config import normalize, evidence_scope, load_config
from our_method.method_history_feedback.demo import BAD, GOOD, DemoProvider, claim, configuration, quote, task
from our_method.method_history_feedback.evidence import attempt_record, validate_citations
from our_method.method_history_feedback.knowledge import adjudicate, retrieve, validate_proposals
from our_method.method_history_feedback.store import KnowledgeStore
from our_method.method_history_feedback.study import initialize, load_public_tasks, preflight, run, write_json


class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="plc-history-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.dataset = self.base / "dataset"
        (self.dataset / "tasks").mkdir(parents=True)
        for tid in ("task-1", "task-2"):
            write_json(self.dataset / "tasks" / (tid + ".json"), task(tid))
        self.root = self.base / "study"

    def init(self, config=None):
        return initialize(self.root, self.dataset, config or configuration(), expected_count=2, shuffle=False)

    def run_study(self, factory=None, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(self.root, provider_factory=factory or (lambda _, order: DemoProvider(order)), **kwargs)

    def json(self, name):
        return json.loads((self.root / name).read_text())

    def test_online_repair_and_next_task_transfer(self):
        self.init()
        report = self.run_study()
        self.assertEqual(report["completed"], 2)
        self.assertEqual(report["passed"], 2)
        self.assertEqual(report["active_claims"], 1)
        self.assertEqual(report["first_candidate_passed"], 1)
        self.assertEqual(report["model_calls_by_role"], {
            "plc.generate": 3, "task.reflect": 1, "knowledge.curate": 2, "knowledge.review": 1})
        first = self.json("runs/0001/model/0001/request.json")["payload"]
        repair = self.json("runs/0001/model/0003/request.json")["payload"]
        second = self.json("runs/0002/model/0001/request.json")["payload"]
        self.assertEqual(first["retrieved_knowledge"], [])
        self.assertEqual(first["current_task_history"], [])
        self.assertIn("include equality", repair["current_task_history"][-1]["observation"])
        self.assertEqual(repair["local_hypothesis"]["status"], "unverified_local_hypothesis")
        self.assertEqual(repair["retrieved_knowledge"], [])
        self.assertEqual(second["current_task_history"], [])
        self.assertTrue(second["retrieved_knowledge"])
        self.assertNotIn("code", second["retrieved_knowledge"][0])
        self.assertNotIn("task-2", json.dumps(self.json("runs/0001/learning/evidence_context.json")))
        self.assertEqual(report["budget_totals"]["model_calls"], 7)

    def test_resume_does_not_repeat_completed_calls_or_publish_twice(self):
        self.init()
        first = self.run_study(max_tasks=1)
        self.assertEqual(first["completed"], 1)
        final = self.run_study()
        again = self.run_study(lambda *_: self.fail("provider called after completion"))
        self.assertEqual(final, again)
        self.assertEqual(final["budget_totals"]["model_calls"], 7)

    def test_finished_task_recovers_after_crash_before_commit(self):
        self.init()
        with patch.object(KnowledgeStore, "commit", side_effect=RuntimeError("injected crash")):
            with self.assertRaises(RuntimeError):
                self.run_study(max_tasks=1)
        self.assertTrue((self.root / "runs/0001/result.json").exists())
        report = self.run_study(lambda *_: self.fail("recovery repeated model call"), max_tasks=1)
        self.assertEqual(report["completed"], 1)
        self.assertEqual(report["active_claims"], 1)

    def test_interrupted_task_cannot_reset_budget(self):
        self.init()
        (self.root / "runs/0001").mkdir(parents=True)
        with self.assertRaisesRegex(ProtocolError, "budget reset"):
            self.run_study()

    def test_changed_public_input_rejected_before_call(self):
        self.init()
        value = self.json("public_tasks/0001.json")
        value["requirement"] += " changed"
        write_json(self.root / "public_tasks/0001.json", value)
        with self.assertRaisesRegex(ProtocolError, "public task changed"):
            self.run_study(lambda *_: self.fail("should not call provider"))

    def test_changed_configuration_rejected(self):
        self.init()
        value = self.json("config.json")
        value["method"]["min_support_tasks"] = 2
        write_json(self.root / "config.json", value)
        with self.assertRaisesRegex(ProtocolError, "configuration or implementation changed"):
            self.run_study()

    def test_public_loader_excludes_hidden_fields_and_paths(self):
        hidden = self.dataset / "evaluator"
        hidden.mkdir()
        (hidden / "task-1.json").write_text("deliberately invalid JSON; must not be opened")
        public = task("task-1")
        public.update(reference_code="PRIVATE_REFERENCE", metadata={"secret_answer": "PRIVATE_REFERENCE"})
        write_json(self.dataset / "tasks/task-1.json", public)
        tasks = load_public_tasks(self.dataset, 2)
        self.assertNotIn("PRIVATE_REFERENCE", json.dumps(tasks))
        public["project_root"] = "../../outside"
        write_json(self.dataset / "tasks/task-1.json", public)
        with self.assertRaisesRegex(ProtocolError, "inline public"):
            load_public_tasks(self.dataset, 2)

    def test_offline_asset_config_rejected_before_loading(self):
        config = configuration()
        config["method"]["cases_path"] = "nonexistent-1000-records.json"
        path = self.base / "config.json"
        write_json(path, config)
        with self.assertRaisesRegex(ProtocolError, "offline asset"):
            load_config(path)

    def test_unknown_does_not_become_program_failure_or_knowledge(self):
        self.init(configuration("unknown"))
        report = self.run_study(max_tasks=1)
        result = self.json("runs/0001/result.json")
        record = self.json("runs/0001/attempts/01/record.json")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["budget"]["candidates"], 1)
        self.assertEqual(result["budget"]["tool_calls"], 2)
        self.assertFalse(record["confirmed_failure"])
        self.assertFalse(record["passed"])
        self.assertEqual(report["active_claims"], 0)

    def test_malformed_final_candidate_does_not_inherit_old_receipts(self):
        config = configuration(reflect_after_attempt=False, learn_cross_task_knowledge=False)
        config["budgets"]["max_candidates"] = 2
        self.init(config)
        provider = ReplayProvider({"responses": [
            {"role": "plc.generate", "response": {"base_code_sha256": content_hash(""), "code": BAD}},
            {"role": "plc.generate", "response": {"base_code_sha256": "stale", "code": GOOD}}]})
        self.run_study(lambda *_: provider, max_tasks=1)
        result = self.json("runs/0001/result.json")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["checks"], [])
        self.assertEqual(result["budget"]["candidates"], 2)

    def test_model_budget_includes_learning_and_preserves_pass(self):
        config = configuration(reflect_after_attempt=False)
        config["budgets"]["max_model_calls"] = 1
        self.init(config)
        self.run_study(lambda _, order: DemoProvider(order, repair=False), max_tasks=1)
        result = self.json("runs/0001/result.json")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["budget"]["model_calls"], 1)
        self.assertEqual(result["details"]["learning_status"], "rejected_or_budget_exhausted")

    def test_cross_knowledge_ablation_still_repairs_current_task(self):
        self.init(configuration(use_cross_task_knowledge=False))
        report = self.run_study()
        self.assertEqual(report["passed"], 2)
        self.assertEqual(report["tasks"][1]["knowledge_offered_ids"], [])
        self.assertEqual(report["model_calls_by_role"]["task.reflect"], 1)

    def test_strict_tool_receipts_require_hashes_and_execution(self):
        for mode in ("missing_hash", "wrong_hash", "not_executed"):
            with self.subTest(mode=mode):
                self.root = self.base / mode
                self.init(configuration(mode, learn_cross_task_knowledge=False))
                report = self.run_study(max_tasks=1)
                self.assertEqual(report["passed"], 0)
                self.assertEqual(report["tasks"][0]["status"], "incomplete")

    def test_live_preflight_rejects_test_or_unavailable_tools(self):
        config = configuration()
        config["provider"] = {"kind": "openai_compatible", "model": "explicit-test-model",
                              "base_url": "https://example.invalid/v1", "api_key_env": "PLC_TEST_KEY"}
        result = preflight(config, require_environment=False)
        self.assertFalse(result["ready"])
        self.assertTrue(any("synthetic" in e for e in result["errors"]))
        config["validators"] = {}
        self.assertFalse(preflight(config, require_environment=False)["ready"])

    def test_no_feedback_condition_does_not_send_local_tool_history(self):
        config = configuration(use_task_feedback=False, reflect_after_attempt=False,
                               learn_cross_task_knowledge=False, use_cross_task_knowledge=False)
        self.init(config)
        self.run_study(max_tasks=1)
        payload = self.json("runs/0001/model/0002/request.json")["payload"]
        self.assertEqual(payload["current_task_history"], [])
        self.assertIsNone(payload["local_hypothesis"])
        self.assertNotIn("Boundary comparison must include equality", json.dumps(payload))

    def test_history_only_learns_after_task_without_exposing_current_feedback(self):
        self.init(configuration(use_task_feedback=False, reflect_after_attempt=False,
                                learn_cross_task_knowledge=True, use_cross_task_knowledge=True))
        report = self.run_study()
        self.assertEqual(report["active_claims"], 1)
        self.assertNotIn("task.reflect", report["model_calls_by_role"])
        for order in (1, 2):
            requests = [json.loads(p.read_text()) for p in sorted(
                (self.root / "runs" / f"{order:04d}" / "model").glob("*/request.json"))]
            roles = [r["role"] for r in requests]
            for request in requests:
                if request["role"] != "plc.generate":
                    continue
                payload = request["payload"]
                self.assertEqual(payload["current_task_history"], [])
                self.assertIsNone(payload["local_hypothesis"])
                self.assertNotIn("Boundary comparison must include equality", json.dumps(payload))
                self.assertEqual(bool(payload["retrieved_knowledge"]), order == 2)
            self.assertLess(max(i for i, role in enumerate(roles) if role == "plc.generate"),
                            roles.index("knowledge.curate"))

    def test_history_only_rejects_current_task_reflection(self):
        with self.assertRaisesRegex(ProtocolError, "current-task reflection"):
            normalize(configuration(use_task_feedback=False, reflect_after_attempt=True))

    def test_store_future_publication_is_rejected(self):
        manifest = self.init()
        store = KnowledgeStore(self.root / "knowledge.sqlite3", manifest)
        self.addCleanup(store.close)
        with self.assertRaisesRegex(ProtocolError, "earlier task"):
            store.commit({"order": 2, "task_id": "task-2"})
        with self.assertRaisesRegex(ProtocolError, "frozen order"):
            store.commit({"order": 0, "task_id": "task-2"})


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.settings = normalize(configuration())["method"]
        self.scope = evidence_scope(task("a"), normalize(configuration()))
        self.good = {"id": "t0001-a02", "task_id": "a", "order": 1, "code_hash": content_hash(GOOD),
                     "passed": True, "confirmed_failure": False, "code": GOOD,
                     "requirement": task("a")["requirement"], "observation": '{"status":"pass"}'}
        self.available = {self.good["id"]: self.good}

    def admit(self, proposal=None, available=None, parents=None, order=1, minimum=1, verdict="supported"):
        available = available or self.available
        parents = parents or {}
        proposals = validate_proposals({"claims": [proposal or claim()]}, available, parents, 3)
        reviews = {"reviews": [{"proposal_id": p["proposal_id"], "verdict": verdict,
                    "reason": "Scoped observed evidence; semantic review is not a proof.", "evidence": p["evidence"]} for p in proposals]}
        return adjudicate(proposals, reviews, available, parents, self.scope, order, minimum)

    def test_fabricated_or_future_citation_rejected(self):
        for bad in (quote("t9999-a01", '"status":"pass"'), quote(self.good["id"], "not in observation")):
            with self.assertRaises(ProtocolError):
                validate_citations([bad], self.available)
        with self.assertRaises(ProtocolError):
            validate_citations([quote([], '"status":"pass"')], self.available)

    def test_same_task_multiple_attempts_are_not_multiple_task_support(self):
        second = {**self.good, "id": "t0001-a03"}
        available = {**self.available, second["id"]: second}
        proposal = claim()
        proposal["evidence"].append(quote(second["id"], '"status":"pass"'))
        records, _ = self.admit(proposal, available, minimum=2)
        self.assertEqual(records[0]["status"], "draft")
        self.assertEqual(records[0]["support_task_ids"], ["a"])

    def test_draft_can_accumulate_support_but_is_not_generation_memory(self):
        records, _ = self.admit(minimum=2)
        parent = records[0]
        snapshot = {"records": records}
        self.assertEqual(retrieve(snapshot, task("b"), self.scope, self.settings), [])
        self.assertTrue(retrieve(snapshot, task("b"), self.scope, self.settings, for_curation=True))
        second = {**self.good, "id": "t0002-a01", "task_id": "b", "order": 2}
        proposal = claim(second["id"])
        proposal.update(relation="supports", parent_id=parent["id"])
        records, _ = self.admit(proposal, {second["id"]: second}, {parent["id"]: parent}, order=2, minimum=2)
        self.assertEqual(records[0]["id"], parent["id"])
        self.assertEqual(records[0]["status"], "active")
        self.assertEqual(records[0]["support_task_ids"], ["a", "b"])
        self.assertEqual(records[0]["version"], 2)

    def test_insufficient_review_never_activates_a_rule(self):
        records, _ = self.admit(verdict="insufficient")
        self.assertEqual(records[0]["status"], "draft")

    def test_code_quote_without_passing_observation_not_active(self):
        proposal = claim()
        proposal["evidence"] = [quote(self.good["id"], "y := x >= 10;", "code")]
        records, _ = self.admit(proposal)
        self.assertEqual(records[0]["status"], "draft")

    def test_old_success_cannot_validate_a_new_untested_repair(self):
        failed = {**self.good, "id": "t0002-a01", "order": 2, "task_id": "b", "passed": False,
                  "confirmed_failure": True, "observation": '{"status":"fail"}'}
        proposal = claim()
        proposal["evidence"].append(quote(failed["id"], '"status":"fail"'))
        records, _ = self.admit(proposal, {**self.available, failed["id"]: failed}, order=2)
        self.assertEqual(records[0]["status"], "draft")

    def test_successful_retry_uses_latest_stage_verdict_preserving_error(self):
        receipts = [{"stage": "compile", "status": "error", "candidate_id": 1,
                     "code_hash": content_hash(GOOD), "evidence": {"executed": False}}]
        receipts += [{"stage": stage, "status": "pass", "candidate_id": 1,
                      "code_hash": content_hash(GOOD), "evidence": {"executed": True}}
                     for stage in ("compile", "runtime", "formal")]
        record = attempt_record(task("a"), 1, 1, GOOD, receipts)
        self.assertTrue(record["passed"])
        self.assertEqual(len(record["checks"]), 4)

    def test_uncertain_tool_result_never_positive_or_negative_support(self):
        uncertain = {**self.good, "passed": False, "confirmed_failure": False,
                     "observation": '{"status":"unknown"}'}
        proposal = claim()
        proposal["evidence"] = [quote(uncertain["id"], '"status":"unknown"')]
        records, _ = self.admit(proposal, {uncertain["id"]: uncertain})
        self.assertEqual(records[0]["status"], "draft")
        self.assertEqual(records[0]["support_task_ids"], [])

    def test_contradiction_withdraws_parent_without_proving_replacement(self):
        records, _ = self.admit()
        parent = records[0]
        failure = {**self.good, "id": "t0002-a01", "order": 2, "task_id": "b", "passed": False,
                   "confirmed_failure": True, "observation": '{"status":"fail","diagnostics":["counterexample"]}'}
        proposal = claim()
        proposal.update(relation="contradicts", parent_id=parent["id"], text="A scoped counterexample needs investigation.",
                        evidence=[quote(failure["id"], '"status":"fail"')])
        records, _ = self.admit(proposal, {failure["id"]: failure}, {parent["id"]: parent}, order=2)
        self.assertEqual([r["status"] for r in records], ["disputed", "draft"])
        self.assertEqual(parent["status"], "active")
        self.assertEqual(retrieve({"records": records}, task("b"), self.scope, self.settings), [])

    def test_retrieval_enforces_environment_scope(self):
        records, _ = self.admit()
        self.assertTrue(retrieve({"records": records}, task("b"), self.scope, self.settings))
        self.assertFalse(retrieve({"records": records}, task("b"), {**self.scope, "target": "different vendor"}, self.settings))

    def test_support_cannot_silently_edit_existing_rule(self):
        records, _ = self.admit()
        parent = records[0]
        proposal = claim()
        proposal.update(relation="supports", parent_id=parent["id"], text="Changed rule.")
        with self.assertRaisesRegex(ProtocolError, "preserve"):
            self.admit(proposal, parents={parent["id"]: parent})


if __name__ == "__main__":
    unittest.main()
