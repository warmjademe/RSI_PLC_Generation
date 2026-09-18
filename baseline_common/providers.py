"""Model transport only; all baseline-specific prompts live in the methods."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .errors import ProtocolError, ProviderError
from .utils import canonical_json, read_json
from pathlib import Path


class ReplayProvider:
    kind = "replay"
    secrets: list[str] = []

    def __init__(self, config: dict):
        self.responses = read_json(Path(config["path"])) if "path" in config else config.get("responses", [])
        if not isinstance(self.responses, list):
            raise ProtocolError("replay file must contain an array")
        self.position = 0
        self.requested_model = "offline-replay"

    def complete(self, role: str, system: str, payload: dict, *, max_tokens: int, timeout: float) -> dict:
        if self.position >= len(self.responses):
            raise ProviderError("replay exhausted")
        item = self.responses[self.position]
        self.position += 1
        if not isinstance(item, dict) or item.get("role") != role:
            raise ProtocolError(f"replay role mismatch at entry {self.position}")
        if "error" in item:
            raise ProviderError("scripted replay error")
        response = item.get("response")
        text = canonical_json(response) if isinstance(response, dict) else str(response)
        return {"text": text, "model": self.requested_model,
                "usage": item.get("usage", {"input_tokens": len((system + canonical_json(payload)).encode()),
                                             "output_tokens": len(text.encode())}),
                "finish_reason": "stop", "evidence_mode": "replay"}


class HTTPProvider:
    """OpenAI-compatible Chat Completions or native Anthropic Messages, no retries."""

    def __init__(self, config: dict):
        self.config = config
        self.kind = config.get("kind")
        if self.kind not in ("openai_compatible", "anthropic"):
            raise ProtocolError("provider kind must be replay, openai_compatible, or anthropic")
        self.requested_model = config.get("model")
        if not isinstance(self.requested_model, str) or not self.requested_model:
            raise ProtocolError("HTTP provider needs an explicit model")
        if not isinstance(config.get("base_url"), str):
            raise ProtocolError("HTTP provider needs an explicit base_url")
        env_name = config.get("api_key_env")
        if not isinstance(env_name, str) or not env_name:
            raise ProtocolError("HTTP provider needs api_key_env")
        self.key = os.environ.get(env_name, "")
        if not self.key:
            raise ProtocolError(f"configured credential environment variable is unset: {env_name}")
        self.secrets = [self.key]
        allowed = config.get("allowed_resolved_models", [self.requested_model])
        if not isinstance(allowed, list) or not allowed or not all(isinstance(x, str) for x in allowed):
            raise ProtocolError("allowed_resolved_models must be a nonempty string list")
        self.allowed_models = set(allowed)

    def complete(self, role: str, system: str, payload: dict, *, max_tokens: int, timeout: float) -> dict:
        conf = self.config
        base = conf["base_url"].rstrip("/")
        headers = {"Content-Type": "application/json", "User-Agent": "plc-baselines/1.0"}
        system = system + "\nReturn one complete JSON object only. Do not include reasoning outside the requested response schema."
        body = {"model": self.requested_model, "max_tokens": max_tokens,
                "temperature": conf.get("temperature", 0), "stream": False}
        if "top_p" in conf:
            body["top_p"] = conf["top_p"]
        if self.kind == "anthropic":
            url = base + "/messages"
            headers.update({"x-api-key": self.key, "anthropic-version": "2023-06-01"})
            body.update({"system": system, "messages": [{"role": "user", "content": canonical_json(payload)}]})
        else:
            url = base + "/chat/completions"
            headers["Authorization"] = "Bearer " + self.key
            body["messages"] = [{"role": "system", "content": system},
                                {"role": "user", "content": canonical_json(payload)}]
            if conf.get("json_mode", False):
                body["response_format"] = {"type": "json_object"}
            if "seed" in conf:
                body["seed"] = conf["seed"]
        request = urllib.request.Request(url, data=canonical_json(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=min(float(conf.get("timeout_seconds", 120)), timeout)) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ProviderError("provider response exceeded 16 MiB")
                data = json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Do not expose response bodies, request headers, or authenticated URLs.
            raise ProviderError(f"provider HTTP {exc.code}; no automatic retry") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"provider transport failed: {type(exc).__name__}; no automatic retry") from None
        except (ValueError, UnicodeError):
            raise ProviderError("provider returned invalid JSON") from None
        if not isinstance(data, dict):
            raise ProtocolError("provider response must be an object")
        model = data.get("model")
        if model not in self.allowed_models:
            raise ProtocolError("returned model identifier is not in allowed_resolved_models")
        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            raise ProtocolError("provider usage must be an object")
        if self.kind == "anthropic":
            content = data.get("content", [])
            if not isinstance(content, list) or not all(isinstance(x, dict) for x in content):
                raise ProtocolError("Anthropic content must be an array of blocks")
            if any(x.get("type") == "text" and not isinstance(x.get("text"), str) for x in content):
                raise ProtocolError("Anthropic text blocks must contain strings")
            text = "".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
            input_parts = [usage.get("input_tokens"), usage.get("cache_read_input_tokens", 0),
                           usage.get("cache_creation_input_tokens", 0)]
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in input_parts):
                raise ProtocolError("Anthropic input/cache token counts must be nonnegative integers")
            normalized = {"input_tokens": sum(input_parts), "output_tokens": usage.get("output_tokens")}
            reason = data.get("stop_reason")
            if reason not in ("end_turn", "stop_sequence"):
                return {"text": text, "model": model, "usage": normalized, "finish_reason": reason,
                        "invalid_finish": True, "provider_usage": usage}
        else:
            choices = data.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ProtocolError("provider must return exactly one completion")
            message = choices[0].get("message")
            if not isinstance(message, dict):
                raise ProtocolError("completion message must be an object")
            text = message.get("content")
            normalized = {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}
            reason = choices[0].get("finish_reason")
            if reason != "stop":
                return {"text": text, "model": model, "usage": normalized, "finish_reason": reason,
                        "invalid_finish": True, "provider_usage": usage}
        if not isinstance(text, str) or not text.strip():
            raise ProtocolError("provider completion has no text")
        return {"text": text, "model": model, "usage": normalized, "finish_reason": reason,
                "provider_usage": usage, "evidence_mode": "live_model"}


def create_provider(config: dict):
    return ReplayProvider(config) if config.get("kind", "replay") == "replay" else HTTPProvider(config)
