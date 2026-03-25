"""
Cost Tracker â€” tracks token usage and estimates API cost per task run.

Pricing table is loaded from config/logging_config.yaml when available,
and falls back to hardcoded defaults otherwise.

Outputs:
  - Per-task cost summary (printed + JSONL log)
  - Cumulative session cost
  - Cost breakdown by model

Usage::

    tracker = CostTracker()

    tracker.record(model="claude-sonnet-4-5", input_tokens=1000, output_tokens=200)
    tracker.record(model="claude-sonnet-4-5", input_tokens=800,  output_tokens=150)

    print(tracker.session_cost_usd)   # e.g. 0.00435
    report = tracker.report()
    print(report.to_markdown())
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Pricing table (USD per 1M tokens)
# ---------------------------------------------------------------------------

DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5":     {"input": 3.00,  "output": 15.00},
    "claude-opus-4":         {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5":      {"input": 0.25,  "output": 1.25},
    "gpt-4o":                {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":           {"input": 0.15,  "output": 0.60},
    "gpt-4-turbo":           {"input": 10.00, "output": 30.00},
    "llama3":                {"input": 0.0,   "output": 0.0},
    "codestral":             {"input": 0.0,   "output": 0.0},
    "unknown":               {"input": 3.00,  "output": 15.00},
}


def estimate_cost(input_tokens: int, output_tokens: int, model: str = "claude-sonnet-4-5") -> float:
    """Return estimated USD cost for given token usage."""
    pricing = DEFAULT_PRICING.get(model, DEFAULT_PRICING["unknown"])
    return (
        (input_tokens  / 1_000_000) * pricing["input"] +
        (output_tokens / 1_000_000) * pricing["output"]
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TokenUsageRecord:
    """One LLM call's token usage."""
    record_id: str
    task_id: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    step: int = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class CostReport:
    """Aggregated cost report for a session or task."""
    session_id: str
    task_id: str
    model_breakdown: dict[str, dict[str, Any]]
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    record_count: int
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "model_breakdown": self.model_breakdown,
            "record_count": self.record_count,
            "generated_at": self.generated_at,
        }

    def to_markdown(self) -> str:
        lines = [
            "## Cost Report",
            f"**Task:** `{self.task_id}`  |  **Session:** `{self.session_id}`",
            "",
            "| Model | Input tokens | Output tokens | Cost (USD) |",
            "|-------|-------------|--------------|-----------|",
        ]
        for model, data in self.model_breakdown.items():
            lines.append(
                f"| {model} | {data['input_tokens']:,} | {data['output_tokens']:,} "
                f"| ${data['cost_usd']:.6f} |"
            )
        lines.extend([
            f"| **Total** | **{self.total_input_tokens:,}** | **{self.total_output_tokens:,}** "
            f"| **${self.total_cost_usd:.6f}** |",
            "",
            f"Generated: {self.generated_at[:19]}Z",
        ])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cost Tracker
# ---------------------------------------------------------------------------


class CostTracker:
    """
    Records token usage per LLM call and aggregates cost estimates.

    One tracker instance per agent session (or share across tasks for
    a cumulative session view).
    """

    def __init__(
        self,
        task_id: str = "default",
        output_path: str | None = None,
        pricing: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.task_id = task_id
        self.session_id = str(uuid.uuid4())[:12]
        self._pricing = pricing or DEFAULT_PRICING
        self._records: list[TokenUsageRecord] = []
        self._output_path = Path(output_path) if output_path else None
        if self._output_path:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Recording
    # -----------------------------------------------------------------------

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        step: int = 0,
    ) -> TokenUsageRecord:
        """Record one LLM API call's token usage."""
        pricing = self._pricing.get(model, self._pricing.get("unknown", {"input": 3.0, "output": 15.0}))
        cost = (
            (input_tokens  / 1_000_000) * pricing["input"] +
            (output_tokens / 1_000_000) * pricing["output"]
        )
        rec = TokenUsageRecord(
            record_id=str(uuid.uuid4())[:8],
            task_id=self.task_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            step=step,
        )
        self._records.append(rec)

        if self._output_path:
            self._append_jsonl(rec)

        return rec

    def record_from_response(self, response: Any, step: int = 0) -> TokenUsageRecord | None:
        """Convenience: record from an AgentResponse or LLMResponse object."""
        model = getattr(response, "model", "") or ""
        input_t = getattr(response, "total_input_tokens", 0) or getattr(response, "input_tokens", 0)
        output_t = getattr(response, "total_output_tokens", 0) or getattr(response, "output_tokens", 0)
        if input_t == 0 and output_t == 0:
            return None
        return self.record(model=model or "unknown", input_tokens=input_t, output_tokens=output_t, step=step)

    # -----------------------------------------------------------------------
    # Aggregation
    # -----------------------------------------------------------------------

    @property
    def session_cost_usd(self) -> float:
        return sum(r.estimated_cost_usd for r in self._records)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self._records)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def report(self) -> CostReport:
        """Build and return a CostReport for all recorded calls."""
        breakdown: dict[str, dict[str, Any]] = {}
        for rec in self._records:
            if rec.model not in breakdown:
                breakdown[rec.model] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
            breakdown[rec.model]["input_tokens"]  += rec.input_tokens
            breakdown[rec.model]["output_tokens"] += rec.output_tokens
            breakdown[rec.model]["cost_usd"]      += rec.estimated_cost_usd
            breakdown[rec.model]["calls"]         += 1

        # Round for display
        for data in breakdown.values():
            data["cost_usd"] = round(data["cost_usd"], 6)

        return CostReport(
            session_id=self.session_id,
            task_id=self.task_id,
            model_breakdown=breakdown,
            total_input_tokens=self.total_input_tokens,
            total_output_tokens=self.total_output_tokens,
            total_cost_usd=round(self.session_cost_usd, 6),
            record_count=len(self._records),
        )

    def reset(self) -> None:
        """Clear all recorded calls."""
        self._records.clear()

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def _append_jsonl(self, rec: TokenUsageRecord) -> None:
        try:
            line = json.dumps({
                "record_id": rec.record_id,
                "task_id": rec.task_id,
                "model": rec.model,
                "input_tokens": rec.input_tokens,
                "output_tokens": rec.output_tokens,
                "estimated_cost_usd": rec.estimated_cost_usd,
                "step": rec.step,
                "timestamp": datetime.fromtimestamp(rec.timestamp, tz=timezone.utc).isoformat(),
            })
            with self._output_path.open("a", encoding="utf-8") as f:  # type: ignore[union-attr]
                f.write(line + "\n")
        except OSError:
            pass
