"""
Context State Machine â€” automates compression decisions based on utilization.

Five states:

  NORMAL         < 60%   â€” no action needed
  MONITOR       60â€“70%   â€” log; no action yet
  COMPRESS      70â€“80%   â€” sliding window (drop oldest)
  COMPRESS_HEAVY 80â€“90%  â€” summarization + sliding window
  EMERGENCY      â‰¥ 90%   â€” discard all but system messages + last 4 turns

State is evaluated on every call to `tick()`. Transitions are logged.

Usage::

    tracker = ContextBudgetTracker()
    sm = ContextStateMachine(tracker=tracker, llm=my_llm)

    # Before each LLM call:
    messages = await sm.tick(messages)
    # messages is now guaranteed to be within budget
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from loguru import logger

from agent.cost.context_budget import ContextBudgetTracker
from agent.cost.windowing import SlidingWindow, SummarizationWindow


# ---------------------------------------------------------------------------
# States and thresholds
# ---------------------------------------------------------------------------


class ContextState(Enum):
    NORMAL         = auto()   # < 60%
    MONITOR        = auto()   # 60â€“70%
    COMPRESS       = auto()   # 70â€“80%
    COMPRESS_HEAVY = auto()   # 80â€“90%
    EMERGENCY      = auto()   # â‰¥ 90%


# (lower_bound_inclusive, upper_bound_exclusive)
_THRESHOLDS: list[tuple[ContextState, float, float]] = [
    (ContextState.NORMAL,         0.0,  60.0),
    (ContextState.MONITOR,       60.0,  70.0),
    (ContextState.COMPRESS,      70.0,  80.0),
    (ContextState.COMPRESS_HEAVY,80.0,  90.0),
    (ContextState.EMERGENCY,     90.0, 101.0),
]


def _classify(utilization_pct: float) -> ContextState:
    for state, lo, hi in _THRESHOLDS:
        if lo <= utilization_pct < hi:
            return state
    return ContextState.EMERGENCY


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@dataclass
class ContextStateMachine:
    """
    Evaluates context utilization on every `tick()` and applies the
    appropriate compression strategy.

    Args:
        tracker:            ContextBudgetTracker instance (shared with caller).
        llm:                LLM instance for summarization (optional).
        compress_max_tokens: Token target for COMPRESS state.
        compress_heavy_tokens: Token target for COMPRESS_HEAVY state.
    """

    tracker: ContextBudgetTracker
    llm: Any = field(default=None)
    compress_max_tokens: int = 6_000
    compress_heavy_tokens: int = 5_000

    state: ContextState = field(default=ContextState.NORMAL, init=False)

    async def tick(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Evaluate context state and apply compression if needed.

        This is the main entry point â€” call it before every LLM API call.

        Args:
            messages: Full conversation history (may include system messages).

        Returns:
            Possibly compressed message list safe to pass to the LLM.
        """
        # Update tracker with current history
        self.tracker.update(history=messages)
        new_state = _classify(self.tracker.utilization_pct)

        if new_state != self.state:
            logger.info(
                f"[context_sm] State: {self.state.name} â†’ {new_state.name} "
                f"({self.tracker.utilization_pct}% utilization)"
            )
            self.state = new_state

        return await self._apply(messages)

    async def _apply(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.state == ContextState.NORMAL:
            return messages

        elif self.state == ContextState.MONITOR:
            logger.debug(
                f"[context_sm] MONITOR â€” {self.tracker.utilization_pct}% "
                f"(threshold: 60%). No action yet."
            )
            return messages

        elif self.state == ContextState.COMPRESS:
            logger.info(
                f"[context_sm] COMPRESS â€” applying sliding window "
                f"(target: {self.compress_max_tokens:,} tokens)"
            )
            sw = SlidingWindow(max_tokens=self.compress_max_tokens)
            return sw.apply(messages)

        elif self.state == ContextState.COMPRESS_HEAVY:
            logger.info("[context_sm] COMPRESS_HEAVY â€” summarizing + sliding window")
            result = messages
            if self.llm is not None:
                sumw = SummarizationWindow(
                    self.llm, max_tokens=self.compress_max_tokens
                )
                result = await sumw.apply(result)
            sw = SlidingWindow(max_tokens=self.compress_heavy_tokens)
            return sw.apply(result)

        elif self.state == ContextState.EMERGENCY:
            logger.warning(
                "[context_sm] ðŸš¨ EMERGENCY compression â€” "
                "discarding all but system messages + last 4 turns"
            )
            system = [m for m in messages if m["role"] == "system"]
            recent = [m for m in messages if m["role"] != "system"][-4:]
            return system + recent

        return messages

    def current_state(self) -> ContextState:
        return self.state

    def is_critical(self) -> bool:
        return self.state in (ContextState.COMPRESS_HEAVY, ContextState.EMERGENCY)
