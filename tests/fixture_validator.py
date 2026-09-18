"""Synthetic tool for subprocess integration tests. Never use as a PLC oracle."""
import json
import sys
from pathlib import Path

request = json.load(sys.stdin)
mode = sys.argv[1] if len(sys.argv) > 1 else "pass"
if mode == "mutate":
    Path(request["entry_file"]).write_text("changed by test tool", encoding="utf-8")
if mode == "mutate_plan":
    Path(sys.argv[2]).write_text("{}", encoding="utf-8")
if mode == "mutate_request":
    Path(sys.argv[2]).write_text("{}", encoding="utf-8")
if mode == "invalid_json":
    print("not a JSON result")
elif mode == "sleep":
    import time
    time.sleep(5)
else:
    result = {"stage": request["stage"], "status": "unknown" if mode == "unknown" else "fail" if mode == "fail" else "pass",
              "code_hash": "wrong" if mode == "wrong_hash" else request["code_hash"],
              "project_hash": request["project_hash"], "plan_hash": request["plan_hash"],
              "properties_hash": request["properties_hash"], "diagnostics": [],
              "evidence": {"fixture_only": True, "not_a_plc_validator": True}}
    print(json.dumps(result))
if mode == "nonzero_pass":
    raise SystemExit(1)
