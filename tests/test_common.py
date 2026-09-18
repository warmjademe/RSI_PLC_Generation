from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from baseline_common import BudgetExceeded, ProtocolError, RunContext, apply_patches, rank_records
from baseline_common.config import load_config, load_task
from baseline_common.providers import HTTPProvider
from baseline_common.utils import Redactor, object_hash


ROOT = Path(__file__).resolve().parents[1]
TOOL = str(Path(__file__).with_name("fixture_validator.py"))


def task():
    return {"id": "synthetic", "requirement": "An explicitly synthetic protocol task.", "target": "test",
            "interface": {}, "entry_file": "src/main.st", "files": {"src/main.st": "original", "lib.st": "read-only"},
            "editable_files": ["src/main.st"], "public_properties": []}


def tool_config(mode="pass", **kwargs):
    return {"kind": "command", "command": [sys.executable, TOOL, mode], "protocol": "json", "test_only": True, **kwargs}


class CommonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self, **kwargs):
        self.n += 1
        config = {"provider": {"kind": "replay", "responses": []}, "validators": {"compile": tool_config()}, **kwargs}
        return RunContext(task(), config, self.base / f"run{self.n}", method="test")

    def test_task_projection_excludes_judge_and_reference(self):
        raw = {**task(), "reference_code": "DO NOT FORWARD", "hidden_tests": ["SECRET"], "metadata": {"answer": "hidden"}}
        p = self.base / "task.json"
        p.write_text(json.dumps(raw))
        projected = load_task(p)
        self.assertNotIn("reference_code", projected)
        self.assertNotIn("hidden_tests", projected)
        self.assertNotIn("metadata", projected)

    def test_explicit_project_allowlist_only(self):
        project = self.base / "project"
        project.mkdir()
        (project / "main.st").write_text("public")
        (project / "private.txt").write_text("do not read")
        raw = {**task(), "files": {}, "entry_file": "main.st", "editable_files": ["main.st"],
               "project_root": "project", "project_files": ["main.st"]}
        p = self.base / "task.json"
        p.write_text(json.dumps(raw))
        self.assertEqual(load_task(p)["files"], {"main.st": "public"})
        raw["project_files"] = ["../private.txt"]
        p.write_text(json.dumps(raw))
        with self.assertRaises(ProtocolError):
            load_task(p)

    def test_symlink_escape_rejected(self):
        project = self.base / "project"
        project.mkdir()
        (self.base / "private").write_text("private")
        (project / "main.st").symlink_to(self.base / "private")
        p = self.base / "task.json"
        p.write_text(json.dumps({**task(), "files": {}, "project_root": "project", "project_files": ["main.st"]}))
        with self.assertRaises(ProtocolError):
            load_task(p)

    def test_config_relative_paths_and_literal_key_rejection(self):
        (self.base / "bank.json").write_text('[{"id":"a"}]')
        p = self.base / "config.json"
        p.write_text(json.dumps({"provider": {"kind": "replay", "path": "responses.json"}, "method": {"cases_path": "bank.json"}}))
        c = load_config(p)
        self.assertEqual(c["method"]["cases"][0]["id"], "a")
        self.assertEqual(c["provider"]["path"], str(self.base / "responses.json"))
        p.write_text('{"provider":{"kind":"anthropic","api_key":"do-not-save"}}')
        with self.assertRaises(ProtocolError):
            load_config(p)

    def test_unknown_validator_is_not_pass(self):
        ctx = self.ctx(validators={})
        receipt = ctx.check("compile", "candidate")
        self.assertEqual(receipt["status"], "unknown")
        with self.assertRaises(ProtocolError):
            ctx.finish("candidate", "passed", "invalid", checks=[receipt])

    def test_real_subprocess_receipt_and_replay_label(self):
        ctx = self.ctx()
        receipt = ctx.check("compile", "candidate")
        result = ctx.finish("candidate", "passed", "fixture only", checks=[receipt])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["evidence_mode"], "replay")
        self.assertIsNone(result["benchmark_score"])
        self.assertTrue(receipt["evidence"]["test_only"])

    def test_forged_or_mutated_receipt_rejected(self):
        ctx = self.ctx()
        receipt = ctx.check("compile", "candidate")
        receipt["diagnostics"] = ["forged"]
        with self.assertRaises(ProtocolError):
            ctx.finish("candidate", "passed", "invalid", checks=[receipt])

    def test_stale_candidate_and_superseded_receipts_rejected(self):
        ctx = self.ctx()
        receipt = ctx.check("compile", "candidate")
        with self.assertRaises(ProtocolError):
            ctx.finish("new candidate", "passed", "invalid", checks=[receipt])
        ctx.config["validators"]["compile"] = tool_config("fail")
        ctx.check("compile", "candidate")
        with self.assertRaises(ProtocolError):
            ctx.finish("candidate", "passed", "old pass", checks=[receipt])

    def test_tool_protocol_failures_are_errors(self):
        for mode in ("wrong_hash", "mutate", "invalid_json", "nonzero_pass"):
            with self.subTest(mode=mode):
                ctx = self.ctx(validators={"compile": tool_config(mode)})
                self.assertEqual(ctx.check("compile", "candidate")["status"], "error")

    def test_validator_timeout_and_missing_executable(self):
        ctx = self.ctx(validators={"compile": tool_config("sleep", timeout_seconds=.05)})
        self.assertEqual(ctx.check("compile", "candidate")["status"], "error")

    def test_invalid_timeout_never_starts_a_child(self):
        ctx = self.ctx(validators={"compile": tool_config(timeout_seconds="invalid")})
        with patch("baseline_common.validators.subprocess.Popen") as popen:
            with self.assertRaises(ProtocolError):
                ctx.check("compile", "candidate")
            popen.assert_not_called()

    def test_mutated_plan_and_request_cannot_keep_a_pass(self):
        for mode, marker in (("mutate_plan", "{plan}"), ("mutate_request", "{request}")):
            conf = tool_config(mode)
            conf["command"].append(marker)
            ctx = self.ctx(validators={"runtime": conf})
            receipt = ctx.check("runtime", "candidate", plan={"cases": [{"id": "original"}]})
            self.assertEqual(receipt["status"], "error")
        ctx = self.ctx(validators={"compile": {"kind": "command", "command": ["/definitely/nonexistent/compiler"]}})
        self.assertEqual(ctx.check("compile", "candidate")["status"], "error")

    def test_exit_code_is_not_a_runtime_oracle(self):
        ctx = self.ctx(validators={"runtime": {"kind": "command", "command": [sys.executable, "-c", "pass"], "protocol": "exit_code"}})
        with self.assertRaises(ProtocolError):
            ctx.check("runtime", "candidate", plan={"cases": []})

    def test_project_permissions_and_plan_hash(self):
        ctx = self.ctx(validators={"runtime": tool_config()})
        with self.assertRaises(ProtocolError):
            ctx.check("compile", "candidate", files={"src/main.st": "candidate", "lib.st": "modified"})
        plan = {"cases": [{"id": "a"}]}
        receipt = ctx.check("runtime", "candidate", plan=plan)
        self.assertEqual(receipt["plan_hash"], object_hash(plan))

    def test_latest_candidate_survives_tool_budget_exhaustion(self):
        ctx = self.ctx(budgets={"max_tool_calls": 1})
        ctx.check("compile", "first")
        with self.assertRaises(BudgetExceeded):
            ctx.check("compile", "newest")
        self.assertEqual(ctx.latest_code, "newest")
        self.assertEqual(json.loads((ctx.output / "latest_candidate.json").read_text())["code"], "newest")

    def test_five_candidates_share_budget_across_check_stages(self):
        ctx = self.ctx(validators={"compile": tool_config(), "formal": tool_config()})
        for number in range(1, 6):
            self.assertEqual(ctx.begin_candidate("test generation or plan restart"), number)
            code = f"candidate {number}"
            compile_receipt = ctx.check("compile", code)
            formal_receipt = ctx.check("formal", code, properties=[{"id": "P"}])
            self.assertEqual(ctx.budget.candidates, number)
            self.assertEqual(compile_receipt["candidate_id"], formal_receipt["candidate_id"])
        with self.assertRaises(BudgetExceeded):
            ctx.begin_candidate("sixth")
        self.assertEqual(ctx.budget.candidates, 5)
        self.assertEqual(ctx.budget.tool_calls, 10)
        result = ctx.finish(code, "passed", "synthetic receipts only", checks=[compile_receipt, formal_receipt])
        self.assertEqual(len(result["candidate_results"]), 5)
        self.assertEqual(result["candidate_results"][-1]["last_status_by_stage"], {"compile": "pass", "formal": "pass"})

    def test_candidate_hard_cap_and_unregistered_sixth_version(self):
        with self.assertRaises(ProtocolError):
            self.ctx(budgets={"max_candidates": 6})
        ctx = self.ctx()
        for number in range(5):
            ctx.check("compile", f"candidate {number}")
        with self.assertRaises(BudgetExceeded):
            ctx.check("compile", "sixth is not admitted")
        self.assertEqual(ctx.latest_code, "candidate 4")
        self.assertEqual(ctx.budget.tool_calls, 5)

    def test_model_budget_failure_and_malformed_response_charge_attempts(self):
        ctx = self.ctx(provider={"kind": "replay", "responses": [{"role": "r", "response": "broken JSON"}]}, budgets={"max_model_calls": 1})
        with self.assertRaises(ProtocolError):
            ctx.ask("r", "system", {})
        self.assertEqual(ctx.budget.model_calls, 1)
        self.assertGreater(ctx.budget.output_tokens, 0)
        with self.assertRaises(BudgetExceeded):
            ctx.ask("r", "system", {})
        self.assertEqual(ctx.budget.model_calls, 1)

    def test_failed_transport_is_counted_without_retry(self):
        ctx = self.ctx(provider={"kind": "replay", "responses": [{"role": "r", "error": "synthetic"}]})
        with self.assertRaises(Exception):
            ctx.ask("r", "system", {})
        self.assertEqual(ctx.provider.position, 1)
        self.assertEqual(ctx.budget.estimated_charges, 1)

    def test_redaction_and_ambiguous_patch_rejection(self):
        redact = Redactor(["test-private-value"])
        self.assertEqual(redact.clean({"nested": ["test-private-value"]}), {"nested": ["[REDACTED]"]})
        with self.assertRaises(ProtocolError):
            apply_patches("A A", [{"old": "A", "new": "B"}])
        self.assertEqual(apply_patches("A B", [{"old": "A", "new": "C"}]), "C B")

    def test_bm25_retrieves_content_and_preserves_ties(self):
        records = [{"id": "first", "description": "电机停止优先"}, {"id": "second", "description": "水箱温度"}]
        self.assertEqual(rank_records("电机停止", records, 1)[0]["id"], "first")
        self.assertEqual(rank_records("unrelated", records, 2), records)

    def test_existing_output_is_not_overwritten(self):
        output = self.base / "existing"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            RunContext(task(), {"provider": {"kind": "replay", "responses": []}}, output)
        self.assertEqual(marker.read_text(), "keep")


class HTTPTransportTests(unittest.TestCase):
    def provider(self, kind="openai_compatible"):
        with patch.dict(os.environ, {"PLC_TEST_ONLY_KEY": "synthetic-local-token"}):
            return HTTPProvider({"kind": kind, "base_url": "http://127.0.0.1:1/v1", "model": "test-model",
                                 "api_key_env": "PLC_TEST_ONLY_KEY"})

    def test_malformed_http_shapes_become_protocol_errors(self):
        for response in (
            {"model": "test-model", "usage": None},
            {"model": "test-model", "usage": {}, "choices": [None]},
            {"model": "test-model", "usage": {}, "choices": [{"message": None}]},
        ):
            with self.subTest(response=response), patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(response).encode())):
                with self.assertRaises(ProtocolError):
                    self.provider().complete("r", "system", {}, max_tokens=100, timeout=1)

    def test_anthropic_cache_tokens_count_towards_total(self):
        response = {"model": "test-model", "content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20, "output_tokens": 2}}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(response).encode())):
            result = self.provider("anthropic").complete("r", "system", {}, max_tokens=100, timeout=1)
        self.assertEqual(result["usage"]["input_tokens"], 125)
        self.assertEqual(result["provider_usage"]["cache_read_input_tokens"], 100)

    def test_both_protocols_and_exact_model_identity(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, body, dict(self.headers)))
                model = "unexpected" if body["model"] == "reject-model" else body["model"]
                if self.path.endswith("/messages"):
                    response = {"model": model, "content": [{"type": "text", "text": '{"answer":1}'}],
                                "stop_reason": "end_turn", "usage": {"input_tokens": 7, "output_tokens": 3}}
                else:
                    response = {"model": model, "choices": [{"message": {"content": '{"answer":1}'}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {"PLC_TEST_ONLY_KEY": "synthetic-local-token"}):
                for kind in ("openai_compatible", "anthropic"):
                    config = {"kind": kind, "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                              "model": "test-model", "api_key_env": "PLC_TEST_ONLY_KEY"}
                    p = HTTPProvider(config)
                    response = p.complete("r", "system", {"q": 1}, max_tokens=100, timeout=3)
                    self.assertEqual(response["usage"], {"input_tokens": 7, "output_tokens": 3})
                    self.assertNotIn("synthetic-local-token", json.dumps(captured[-1][1]))
                p = HTTPProvider({**config, "model": "reject-model"})
                with self.assertRaises(ProtocolError):
                    p.complete("r", "system", {}, max_tokens=100, timeout=3)
                self.assertEqual(captured[0][0], "/v1/chat/completions")
                self.assertEqual(captured[1][0], "/v1/messages")
                self.assertIn("system", captured[1][1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
