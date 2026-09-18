"""Errors shared by the three independently implemented PLC baselines."""


class ProtocolError(RuntimeError):
    """Malformed input, response, configuration, or evidence."""


class BudgetExceeded(RuntimeError):
    """A configured task budget has been exhausted."""


class ProviderError(RuntimeError):
    """A model request failed; the attempt still counts against the budget."""


class ModelResponseError(ProtocolError):
    """A received completion cannot be used as the requested candidate/document."""
