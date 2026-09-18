"""A separate online protocol; never loads the previous 1000-task assets."""
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

from baseline_common.budget import Budget
from baseline_common.errors import ProtocolError
from baseline_common.utils import Redactor, object_hash


DEFAULTS = {
    "two_level_feedback": False,
    "use_task_feedback": True,
    "use_cross_task_knowledge": True,
    "learn_cross_task_knowledge": True,
    "reflect_after_attempt": True,
    "unknown_tool_retries": 1,
    "max_local_attempts": 4,
    "max_knowledge_items": 4,
    "knowledge_char_budget": 10000,
    "max_curation_attempts": 4,
    "max_new_claims": 3,
    "min_support_tasks": 1,
}


def normalize(config):
    if not isinstance(config, dict):
        raise ProtocolError("config must be an object")
    out = copy.deepcopy(config)
    if out.get("memory_root") or out.get("corpus"):
        raise ProtocolError("online learning must start without an offline corpus or memory_root")
    settings = out.get("method", {})
    if not isinstance(settings, dict) or set(settings) - set(DEFAULTS):
        raise ProtocolError("unknown history-feedback method setting; offline asset options are not accepted")
    out["method"] = {**DEFAULTS, **settings}
    for key, default in DEFAULTS.items():
        value = out["method"][key]
        if isinstance(default, bool):
            if type(value) is not bool:
                raise ProtocolError(key + " must be boolean")
        elif type(value) is not int or value < (0 if key == "unknown_tool_retries" else 1):
            raise ProtocolError(key + " must be a bounded integer")
    limits = {"unknown_tool_retries": 3, "max_local_attempts": 8,
              "max_knowledge_items": 8, "knowledge_char_budget": 20000,
              "max_curation_attempts": 8, "max_new_claims": 5, "min_support_tasks": 20}
    if any(out["method"][k] > v for k, v in limits.items()):
        raise ProtocolError("method setting exceeds the supported context/retry bound")
    if not out["method"]["use_task_feedback"] and out["method"]["reflect_after_attempt"]:
        raise ProtocolError("no-feedback condition must disable current-task reflection")
    if out["method"]["two_level_feedback"]:
        # Diagnosis is part of the generation response, judged by the next tools.
        out["method"]["reflect_after_attempt"] = False
    # Post-task curation may inspect private checks after generation has ended.
    # Its publication is available only to later tasks, including in NoFeedback.
    out.setdefault("protocol_candidate_limit", 10)
    out.setdefault("budgets", {})
    out["budgets"] = {"max_candidates": 10, "max_model_calls": 64,
                      "max_tool_calls": 120, "max_total_tokens": 2000000,
                      "max_output_tokens": 16384, "max_wall_seconds": 86400,
                      **out["budgets"]}
    Budget(out["budgets"], candidate_limit=out["protocol_candidate_limit"])
    out.setdefault("validation_stages", ["compile", "runtime", "formal"])
    if out["validation_stages"] not in (["compile", "runtime"], ["compile", "runtime", "formal"]):
        raise ProtocolError("PLC validation must be compile -> runtime, optionally followed by formal")
    if not isinstance(out.get("validators", {}), dict):
        raise ProtocolError("validators must be an object")
    if Redactor().clean(out) != out:
        raise ProtocolError("configuration contains a recognizable credential")
    provider = out.get("provider", {})
    if not isinstance(provider, dict) or any(k in provider for k in ("api_key", "password", "token", "headers")):
        raise ProtocolError("provider credentials must use api_key_env")
    if "base_url" in provider:
        if not isinstance(provider["base_url"], str):
            raise ProtocolError("base_url must be a string")
        url = urlsplit(provider["base_url"])
        if url.scheme not in ("https", "http") or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ProtocolError("base_url must be an HTTP(S) URL without credentials/query")
    return out


def load_config(path):
    """Reject offline asset settings before the shared loader can read their files."""
    from baseline_common.config import load_config as common_load
    normalize(json.loads(Path(path).read_text(encoding="utf-8")))
    return normalize(common_load(path))


def evidence_scope(task, config):
    return {"target": task["target"], "language": "st",
            "validator_fingerprint": object_hash({
                "stages": config["validation_stages"], "validators": config.get("validators", {})})}
