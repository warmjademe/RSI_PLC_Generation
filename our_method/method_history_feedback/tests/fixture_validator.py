"""Deliberately synthetic JSON validator, only for workflow tests."""
import json
import sys


def main():
    request = json.load(sys.stdin)
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    fail = "(* WRONG *)" in request["code"] and request["stage"] == "runtime"
    status = "unknown" if mode == "unknown" else "fail" if fail else "pass"
    out = {"stage": request["stage"], "status": status,
           **{k: request[k] for k in ("code_hash", "project_hash", "plan_hash", "properties_hash")},
           "diagnostics": ["Boundary comparison must include equality (synthetic example)."] if fail else [],
           "evidence": {"executed": True, "test_only": True, "not_a_plc_validator": True}}
    if mode == "missing_hash":
        del out["code_hash"]
    if mode == "wrong_hash":
        out["code_hash"] = "different-program"
    if mode == "not_executed":
        out["evidence"]["executed"] = False
    print(json.dumps(out))


if __name__ == "__main__":
    main()
