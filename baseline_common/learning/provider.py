"""Adapt existing learning operators to the same metered provider as generation."""
from dataclasses import dataclass
from pathlib import Path
import json
import threading
import time

from baseline_common.errors import ProviderError, ModelResponseError, ProtocolError


@dataclass(frozen=True)
class ModelCall:
    content: str
    requested_model: str
    resolved_model: str
    usage: dict
    latency_seconds: float
    provider: str
    provider_request_count: int = 1
    resolved_models: tuple = ()

    def sanitized(self):
        return dict(self.__dict__)


def summarize_model_audits(audits):
    usage, models = {}, {}
    for item in audits:
        for key, amount in item.get("usage", {}).items():
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                usage[key] = usage.get(key, 0) + amount
        for name in item.get("resolved_models", []) or [item.get("resolved_model", "")]:
            models[name] = models.get(name, 0) + 1
    return {"model_call_count": len(audits), "provider_request_count": sum(a.get('provider_request_count',1) for a in audits),
            "model_usage": usage, "model_latency_seconds": sum(a.get("latency_seconds", 0) for a in audits),
            "resolved_model_request_counts": models}


class LearningProvider:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.RLock()

    def json_chat(self, messages, *, max_tokens=8192, temperature=0.0):
        # Legacy operators may dispatch concurrently. Serialize budget + artifact IDs.
        # Provider sampling and output limits remain the explicit run configuration.
        with self.lock:
            started = time.monotonic()
            system = next((m["content"] for m in messages if m["role"] == "system"), "Return the requested JSON object.")
            attempts = self.ctx.config.get('learning_response_recovery',{}).get('max_attempts',1)
            if type(attempts) is not int or not 1 <= attempts <= 3:
                raise ProtocolError('learning response recovery permits one to three attempts')
            responses = []
            for attempt in range(attempts):
                hint = min(max_tokens * (2 ** attempt), self.ctx.budget.limits['max_output_tokens'])
                current_system = system
                if attempt:
                    current_system += (
                        '\nThe previous response was truncated or invalid JSON. Return a compact, complete JSON object. '
                        'Shorten explanatory prose while keeping every required field and evidence constraint. '
                        'Do not copy source code unless the requested output schema requires code.'
                    )
                error = None
                try:
                    value = self.ctx.ask("learning.operator", current_system, {
                        "messages": [m for m in messages if m["role"] != "system"],
                        "operator_output_token_hint": hint,
                        "response_instruction": "Return the requested document as a JSON object. Do not wrap it in XML or Markdown.",
                    })
                except ModelResponseError as exc:
                    error = exc
                response_path = self.ctx.output / f'model/{self.ctx.budget.model_calls:04d}/response.json'
                if response_path.exists():
                    responses.append(json.loads(response_path.read_text()))
                if error is None:
                    break
                self.ctx.record('learning_response_rejected', attempt=attempt+1,
                                call=self.ctx.budget.model_calls, reason=str(error), output_token_hint=hint)
                if attempt+1 == attempts:
                    raise error
            response = responses[-1]
            usage = {key:sum(r['usage'][key] for r in responses) for key in ['input_tokens','output_tokens']}
            resolved_models = tuple(dict.fromkeys(r['model'] for r in responses))
            if len(responses)>1:
                self.ctx.record('learning_response_recovered', attempts=len(responses), usage=usage)
            call = ModelCall(json.dumps(value, ensure_ascii=False), self.ctx.provider.requested_model,
                             response["model"], usage, time.monotonic() - started,
                             response.get('provider_name', self.ctx.provider.kind),
                             provider_request_count=sum(r.get('provider_request_count',1) for r in responses),
                             resolved_models=resolved_models)
            return value, call
