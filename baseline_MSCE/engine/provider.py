from baseline_common.errors import ProviderError
from .models import ModelReply


class OpenAIChatProvider:
    """Compatibility name; transport/model selection belongs to the shared context."""
    def __init__(self, provider):
        self.provider = provider

    def complete(self, messages, *, temperature=0.0, max_tokens=8192):
        _, call = self.provider.json_chat(messages, temperature=temperature, max_tokens=max_tokens)
        return ModelReply(call.content, call.provider, call.resolved_model, int(call.latency_seconds * 1000),
                          call.usage.get("input_tokens"), call.usage.get("output_tokens"), raw_usage=call.usage)
