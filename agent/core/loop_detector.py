"""
Loop Detector â€” prevents the agent from spinning in infinite reasoning cycles.

Two complementary detection mechanisms:

1. ToolCallLoopDetector (fast, zero-cost)
   - Hashes tool name + arguments into a fingerprint
   - Raises InfiniteLoopError when the same fingerprint appears
     `repeat_threshold` times within the sliding window
   - Also enforces a hard cap on total steps

2. SemanticLoopDetector (optional, requires embeddings)
   - Embeds each reasoning text turn
   - Raises when `min_trigger_pairs` recent turns are above
     cosine similarity `threshold` â€” catches paraphrased loops

Usage::

    detector = ToolCallLoopDetector()

    for step in reasoning_loop:
        detector.record(step.tool_calls[0])   # raises InfiniteLoopError if stuck
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

from agent.core.exceptions import InfiniteLoopError


# ---------------------------------------------------------------------------
# ToolCall fingerprint
# ---------------------------------------------------------------------------


@dataclass
class ToolCallRecord:
    """Lightweight record of one tool invocation for loop detection."""

    tool_name: str
    arguments: dict[str, Any]

    @property
    def fingerprint(self) -> str:
        """Stable MD5 of tool name + sorted arguments JSON."""
        payload = json.dumps(
            {"tool": self.tool_name, "args": self.arguments},
            sort_keys=True,
        )
        return hashlib.md5(payload.encode()).hexdigest()

    @classmethod
    def from_tool_call(cls, tc: Any) -> "ToolCallRecord":
        """Create from an agent.core.state.ToolCall object."""
        return cls(
            tool_name=tc.tool_name,
            arguments=dict(tc.tool_input or {}),
        )


# ---------------------------------------------------------------------------
# Tool-call loop detector
# ---------------------------------------------------------------------------


class ToolCallLoopDetector:
    """
    Detects infinite loops via tool-call fingerprint repetition.

    Args:
        max_total_steps:  Hard cap on total agent turns (default 50).
        window_size:      Recent history window to examine for cycles (default 10).
        repeat_threshold: How many times the same fingerprint must appear
                          in the window before raising (default 3).
    """

    def __init__(
        self,
        max_total_steps: int = 50,
        window_size: int = 10,
        repeat_threshold: int = 3,
    ) -> None:
        self.max_total_steps = max_total_steps
        self.window_size = window_size
        self.repeat_threshold = repeat_threshold

        self._step: int = 0
        self._history: deque[ToolCallRecord] = deque(maxlen=window_size)
        self._global_counts: Counter[str] = Counter()

    @property
    def step(self) -> int:
        return self._step

    def record(self, call: ToolCallRecord) -> None:
        """
        Record a tool call and check for loop conditions.

        Raises:
            InfiniteLoopError: if a loop is detected or max_steps exceeded.
        """
        self._step += 1

        # 1. Hard step cap
        if self._step > self.max_total_steps:
            raise InfiniteLoopError(
                loop_length=0,
                step=self._step,
                context={"reason": "max_total_steps exceeded", "step": self._step},
            )

        fp = call.fingerprint
        self._history.append(call)
        self._global_counts[fp] += 1

        # 2. Sliding-window repetition check
        recent_fps = [c.fingerprint for c in self._history]
        window_counts = Counter(recent_fps)
        most_common_fp, most_common_n = window_counts.most_common(1)[0]

        if most_common_n >= self.repeat_threshold:
            cycle_len = self._detect_cycle_length(recent_fps)
            raise InfiniteLoopError(
                loop_length=cycle_len,
                step=self._step,
                context={
                    "reason": "repeated_tool_call",
                    "fingerprint": most_common_fp,
                    "repeat_count": most_common_n,
                    "recent_tools": [c.tool_name for c in self._history],
                },
            )

    def record_from_state(self, tool_calls: list[Any]) -> None:
        """Convenience: record all tool calls from a reasoning step."""
        for tc in tool_calls:
            self.record(ToolCallRecord.from_tool_call(tc))

    def reset(self) -> None:
        self._step = 0
        self._history.clear()
        self._global_counts.clear()

    def summary(self) -> dict[str, Any]:
        return {
            "total_steps": self._step,
            "unique_tool_calls": len(self._global_counts),
            "most_repeated": self._global_counts.most_common(1)[0] if self._global_counts else None,
        }

    # -----------------------------------------------------------------------
    # Private
    # -----------------------------------------------------------------------

    def _detect_cycle_length(self, fps: list[str]) -> int:
        """Find the shortest repeating suffix pattern, else return half window."""
        n = len(fps)
        for length in range(1, n // 2 + 1):
            if fps[-length:] == fps[-(length * 2):-length]:
                return length
        return max(1, n // 2)


# ---------------------------------------------------------------------------
# Semantic loop detector (optional â€” requires numpy)
# ---------------------------------------------------------------------------


class SemanticLoopDetector:
    """
    Detects paraphrased loops using cosine similarity between reasoning embeddings.

    Uses a stub embedding by default (hash-seeded random vector).
    Replace `_embed()` with a real embedding call in production.

    Args:
        window:               Number of recent turns to compare against.
        similarity_threshold: Cosine similarity above which turns are "equivalent".
        min_trigger_pairs:    How many similar pairs needed to raise.
    """

    def __init__(
        self,
        window: int = 6,
        similarity_threshold: float = 0.93,
        min_trigger_pairs: int = 2,
    ) -> None:
        self.window = window
        self.similarity_threshold = similarity_threshold
        self.min_trigger_pairs = min_trigger_pairs
        self._embeddings: deque[tuple[str, Any]] = deque(maxlen=window)
        self._step = 0

    def record(self, reasoning_text: str) -> None:
        """
        Add a reasoning turn and check for semantic repetition.

        Raises:
            InfiniteLoopError: if min_trigger_pairs turns are too similar.
        """
        self._step += 1
        vec = self._embed(reasoning_text)
        similar_pairs = 0

        for _prev_text, prev_vec in self._embeddings:
            sim = self._cosine(vec, prev_vec)
            if sim >= self.similarity_threshold:
                similar_pairs += 1

        self._embeddings.append((reasoning_text[:100], vec))

        if similar_pairs >= self.min_trigger_pairs:
            raise InfiniteLoopError(
                loop_length=similar_pairs,
                step=self._step,
                context={
                    "reason": "semantic_repetition",
                    "similar_pairs": similar_pairs,
                    "threshold": self.similarity_threshold,
                },
            )

    # -----------------------------------------------------------------------
    # Stub â€” replace with real embeddings in production
    # -----------------------------------------------------------------------

    def _embed(self, text: str) -> Any:
        try:
            import numpy as np  # type: ignore[import]
            rng = np.random.default_rng(abs(hash(text)) % (2**31))
            v = rng.random(512).astype(np.float32)
            norm = np.linalg.norm(v)
            return v / norm if norm > 0 else v
        except ImportError:
            # Fallback: return a simple hash-based pseudo-vector as list
            h = abs(hash(text))
            return [(h >> i) & 1 for i in range(64)]

    def _cosine(self, a: Any, b: Any) -> float:
        try:
            import numpy as np  # type: ignore[import]
            return float(np.dot(a, b))
        except ImportError:
            # Fallback: Jaccard on bits
            matches = sum(x == y for x, y in zip(a, b))
            return matches / len(a) if a else 0.0
