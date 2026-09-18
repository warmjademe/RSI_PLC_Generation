"""A deterministic two-task example; NO network or actual PLC correctness claims."""
import json
from pathlib import Path
import tempfile

from baseline_common.providers import ReplayProvider
from baseline_common.utils import canonical_json, content_hash
from .study import initialize, run, write_json

BAD = "FUNCTION_BLOCK Boundary\nVAR_INPUT x : INT; END_VAR\nVAR_OUTPUT y : BOOL; END_VAR\ny := x > 10; (* WRONG *)\nEND_FUNCTION_BLOCK"
GOOD = BAD.replace("x > 10; (* WRONG *)", "x >= 10;")


def task(tid):
    return {"id": tid, "language": "ST", "target": "Synthetic boundary task; not a PLC certification",
            "requirement": "Set y when x is greater than or equal to the boundary threshold 10; include equality.",
            "interface": {"entry_pou": "Boundary", "inputs": {"x": "INT"}, "outputs": {"y": "BOOL"}},
            "files": {}, "entry_file": "candidate.st", "editable_files": ["candidate.st"], "public_properties": []}


def configuration(mode="normal", **method):
    validator = {"kind": "command", "protocol": "json", "test_only": True,
                 "command": ["{python}", str(Path(__file__).parent / "tests" / "fixture_validator.py"), mode],
                 "timeout_seconds": 10}
    return {"provider": {"kind": "replay", "responses": []}, "protocol_candidate_limit": 5,
            "budgets": {"max_candidates": 3, "max_model_calls": 12, "max_output_tokens": 2048,
                        "max_total_tokens": 500000, "max_wall_seconds": 120},
            "validators": {s: dict(validator) for s in ("compile", "runtime", "formal")}, "method": method}


def quote(aid, text, field="observation"):
    return {"attempt_id": aid, "field": field, "quote": text}


def claim(aid="t0001-a02"):
    return {"text": "For an inclusive threshold requirement, preserve equality in the boundary comparison.",
            "applies_when": ["The task explicitly requires greater than or equal to a boundary threshold."],
            "does_not_apply_when": ["The requirement specifies a strict comparison or separate hysteresis transitions."],
            "checks": ["Test one value below, exactly at, and one value above the threshold."],
            "keywords": ["boundary", "threshold", "equality", "comparison"],
            "relation": "new", "parent_id": None,
            "evidence": [quote(aid, '"status":"pass"'), quote(aid, "y := x >= 10;", "code")]}


class DemoProvider(ReplayProvider):
    """Respond to the actual supplied hashes/IDs, avoiding stale scripted provenance."""
    def __init__(self, order, *, repair=True, learn=True):
        super().__init__({"responses": []})
        self.order, self.repair, self.learn, self.generated = order, repair, learn, 0

    def complete(self, role, system, payload, *, max_tokens, timeout):
        if role == "plc.generate":
            self.generated += 1
            code = BAD if self.repair and self.order == 1 and self.generated == 1 else GOOD
            response = {"base_code_sha256": payload["base_code_sha256"], "code": code,
                        "change_summary": "Preserve inclusive comparison.",
                        "used_knowledge_ids": [r["id"] for r in payload["retrieved_knowledge"]],
                        "applicability_notes": "This requirement includes threshold equality."}
        elif role == "task.reflect":
            response = {"assumption": "The boundary comparison excludes equality.",
                        "proposed_change": "Use >= for the stated inclusive threshold.",
                        "prediction": "The boundary equality check will pass.", "confidence": "medium",
                        "evidence": [quote(payload["latest_attempt_id"], "Boundary comparison must include equality")]}
        elif role == "knowledge.curate":
            response = {"claims": [claim()] if self.order == 1 and self.learn else []}
        elif role == "knowledge.review":
            response = {"reviews": [{"proposal_id": p["proposal_id"], "verdict": "supported",
                         "reason": "The scoped rule agrees with the public requirement and observed repair; broader applicability is untested.",
                         "evidence": p["evidence"]} for p in payload["proposals"]]}
        else:
            raise AssertionError("unexpected demo role")
        return {"text": canonical_json(response), "model": self.requested_model,
                "usage": {"input_tokens": len(canonical_json(payload).encode()),
                          "output_tokens": len(canonical_json(response).encode())}}


def demo(output):
    with tempfile.TemporaryDirectory(prefix="plc-history-demo-dataset-") as temporary:
        dataset = Path(temporary)
        (dataset / "tasks").mkdir()
        for tid in ("demo-1", "demo-2"):
            write_json(dataset / "tasks" / (tid + ".json"), task(tid))
        initialize(output, dataset, configuration(), expected_count=2, shuffle=False)
        result = run(output, provider_factory=lambda _, order: DemoProvider(order))
    assert result["passed"] == 2 and result["active_claims"] == 1
    assert result["tasks"][1]["knowledge_offered_ids"]
    result["demo_warning"] = "Synthetic scripted responses and marker-based tools; validates control flow only."
    write_json(Path(output) / "demo_result.json", result)
    return result
