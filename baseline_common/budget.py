from __future__ import annotations

import math
import time

from .errors import BudgetExceeded, ProtocolError


class Budget:
    DEFAULTS = {"max_candidates": 5, "max_model_calls": 100, "max_tool_calls": 50,
                "max_total_tokens": 500000, "max_output_tokens": 4096, "max_wall_seconds": 1800}

    def __init__(self, config: dict | None = None, *, candidate_limit: int = 5):
        if type(candidate_limit) is not int or candidate_limit not in (5, 10, 20):
            raise ProtocolError('candidate_limit must identify the 5, 10 or 20 attempt protocol')
        self.limits = {**self.DEFAULTS, **(config or {})}
        for key, value in self.limits.items():
            if key not in self.DEFAULTS:
                raise ProtocolError(f"unknown budget setting: {key}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ProtocolError("budgets must be positive finite numbers")
            if key != "max_wall_seconds" and not isinstance(value, int):
                raise ProtocolError("call and token budgets must be integers")
        if self.limits["max_candidates"] > candidate_limit:
            raise ProtocolError(f"this comparison permits at most {candidate_limit} candidates per task")
        self.started = time.monotonic()
        self.model_calls = self.tool_calls = self.input_tokens = self.output_tokens = 0
        self.candidates = 0
        self.estimated_charges = 0

    def remaining_seconds(self) -> float:
        remaining = self.limits["max_wall_seconds"] - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded("wall-clock budget exhausted")
        return remaining

    def before_model(self, request_text: str, *, output_limit: int | None = None) -> tuple[int, int]:
        self.remaining_seconds()
        if self.model_calls >= self.limits["max_model_calls"]:
            raise BudgetExceeded("model-call budget exhausted")
        # UTF-8 bytes + framing is a conservative admission estimate, not a tokenizer.
        estimate = len(request_text.encode("utf-8")) + 512
        if output_limit is not None and (type(output_limit) is not int or output_limit <= 0):
            raise ProtocolError("per-call output limit must be a positive integer")
        output = min(output_limit or self.limits["max_output_tokens"], self.limits["max_output_tokens"])
        if self.input_tokens + self.output_tokens + estimate + output > self.limits["max_total_tokens"]:
            raise BudgetExceeded("remaining token budget cannot admit this request")
        self.model_calls += 1
        return estimate, output

    def settle_model(self, reservation: tuple[int, int], usage: dict | None) -> None:
        if usage is None:
            inp, out = reservation
            self.estimated_charges += 1
        else:
            inp, out = usage.get("input_tokens"), usage.get("output_tokens")
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (inp, out)):
                self.input_tokens += reservation[0]
                self.output_tokens += reservation[1]
                self.estimated_charges += 1
                raise ProtocolError("provider usage must contain nonnegative integer token counts")
        self.input_tokens += inp
        self.output_tokens += out
        if self.input_tokens + self.output_tokens > self.limits["max_total_tokens"]:
            raise BudgetExceeded("reported usage exhausted total token budget")
        self.remaining_seconds()

    def before_tool(self) -> None:
        self.remaining_seconds()
        if self.tool_calls >= self.limits["max_tool_calls"]:
            raise BudgetExceeded("tool-call budget exhausted")
        self.tool_calls += 1

    def before_candidate(self) -> int:
        self.remaining_seconds()
        if self.candidates >= self.limits["max_candidates"]:
            raise BudgetExceeded(f"candidate budget exhausted (limit {self.limits['max_candidates']})")
        self.candidates += 1
        return self.candidates

    def report(self) -> dict:
        return {"limits": dict(self.limits), "candidates": self.candidates,
                "remaining_candidates": self.limits["max_candidates"] - self.candidates,
                "model_calls": self.model_calls,
                "tool_calls": self.tool_calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens,
                "estimated_charge_calls": self.estimated_charges,
                "elapsed_seconds": round(time.monotonic() - self.started, 6),
                "admission_estimator": "utf8_bytes_plus_512_framing"}
