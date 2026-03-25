"""
Budget Guardrail â€” hard limits that stop the agent before exceeding budget.

Three independent limits (all optional):
  max_tokens_per_run    â€” total tokens in one agent task run
  max_cost_per_run      â€” USD cost ceiling for one task run
  max_cost_cumulative   â€” USD cost ceiling across the entire session

When a limit would be exceeded, `check()` raises `BudgetExceededError`
(or returns (False, reason) depending on `raise_on_exceed` setting).

Usage::

    guardrail = BudgetGuardrail(
        max_tokens_per_run=100_000,
        max_cost_per_run=0.50,
        max_cost_cumulative=5.00,
    )

    # Before each LLM call:
    guardrail.check(
        model="claude-sonnet-4-5",
        input_tokens=800,
        output_tokens=200,
    )

    # After each call:
    guardrail.record(model, input_tokens, output_tokens)

    # Between runs:
    guardrail.reset_run()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from observability.cost_tracker import DEFAULT_PRICING


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class BudgetExceededError(RuntimeError):
    """Raised when a budget guardrail would be violated."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Guardrail
# ---------------------------------------------------------------------------


@dataclass
class BudgetGuardrail:
    """
    Enforces token and cost limits during agent execution.

    Args:
        max_tokens_per_run:   Stop run if total tokens would exceed this.
        max_cost_per_run:     Stop run if USD cost would exceed this.
        max_cost_cumulative:  Stop session if cumulative cost would exceed this.
        raise_on_exceed:      If True, raise BudgetExceededError; else return (False, reason).
        pricing:              Override pricing table (USD per 1M tokens).
    """

    max_tokens_per_run:   int   | None = None
    max_cost_per_run:     float | None = None
    max_cost_cumulative:  float | None = None
    raise_on_exceed:      bool         = True
    pricing:              dict[str, dict[str, float]] = field(
        default_factory=lambda: dict(DEFAULT_PRICING)
    )

    # Mutable state
    _run_tokens:      int   = field(default=0, init=False, repr=False)
    _run_cost:        float = field(default=0.0, init=False, repr=False)
    _cumulative_cost: float = field(default=0.0, init=False, repr=False)
    _run_calls:       int   = field(default=0, init=False, repr=False)
    _total_calls:     int   = field(default=0, init=False, repr=False)

    # â”€â”€ Public API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def check(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> tuple[bool, str]:
        """
        Check whether this LLM call would exceed any limit.

        Does NOT record the call â€” call `record()` after the call completes.

        Returns:
            (True, "Within limits") if allowed.
            (False, reason) if blocked (when raise_on_exceed=False).

        Raises:
            BudgetExceededError: When a limit is exceeded and raise_on_exceed=True.
        """
        projected_tokens = self._run_tokens + input_tokens + output_tokens
        call_cost        = self._estimate_cost(model, input_tokens, output_tokens)
        projected_run    = self._run_cost + call_cost
        projected_cum    = self._cumulative_cost + call_cost

        violations: list[str] = []

        if self.max_tokens_per_run and projected_tokens > self.max_tokens_per_run:
            violations.append(
                f"Token limit: {projected_tokens:,} > {self.max_tokens_per_run:,} tokens/run"
            )

        if self.max_cost_per_run and projected_run > self.max_cost_per_run:
            violations.append(
                f"Run cost limit: ${projected_run:.4f} > ${self.max_cost_per_run:.4f}/run"
            )

        if self.max_cost_cumulative and projected_cum > self.max_cost_cumulative:
            violations.append(
                f"Cumulative cost limit: ${projected_cum:.4f} > ${self.max_cost_cumulative:.4f} total"
            )

        if violations:
            reason = "Budget exceeded â€” " + "; ".join(violations)
            if self.raise_on_exceed:
                raise BudgetExceededError(reason)
            return False, reason

        return True, "Within limits"

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record a completed LLM call â€” update internal counters."""
        cost = self._estimate_cost(model, input_tokens, output_tokens)
        self._run_tokens      += input_tokens + output_tokens
        self._run_cost        += cost
        self._cumulative_cost += cost
        self._run_calls       += 1
        self._total_calls     += 1

    def reset_run(self) -> None:
        """Reset per-run counters between agent task runs."""
        self._run_tokens = 0
        self._run_cost   = 0.0
        self._run_calls  = 0

    # â”€â”€ Properties â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @property
    def run_tokens(self) -> int:
        return self._run_tokens

    @property
    def run_cost_usd(self) -> float:
        return round(self._run_cost, 6)

    @property
    def cumulative_cost_usd(self) -> float:
        return round(self._cumulative_cost, 6)

    @property
    def run_calls(self) -> int:
        return self._run_calls

    # â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = self.pricing.get(model, self.pricing.get("unknown", {"input": 3.0, "output": 15.0}))
        return (
            (input_tokens  / 1_000_000) * pricing["input"] +
            (output_tokens / 1_000_000) * pricing["output"]
        )

    def status(self) -> dict[str, Any]:
        """Return current guardrail status as a dict."""
        return {
            "run_tokens":        self._run_tokens,
            "max_tokens_per_run": self.max_tokens_per_run,
            "run_cost_usd":      self.run_cost_usd,
            "max_cost_per_run":  self.max_cost_per_run,
            "cumulative_cost_usd": self.cumulative_cost_usd,
            "max_cost_cumulative": self.max_cost_cumulative,
            "run_calls":         self._run_calls,
            "total_calls":       self._total_calls,
        }
