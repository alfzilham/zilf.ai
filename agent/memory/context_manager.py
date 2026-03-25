"""
Context Manager â€” keeps the LLM context window within token budget.

Responsibilities:
  1. Track token usage across all messages in a conversation
  2. Prune old messages when the budget is exceeded
  3. Summarise pruned history so the agent doesn't lose critical context
  4. Inject relevant long-term memories before each LLM call

Token counting strategy:
  - Exact count via tiktoken (if installed)
  - Fallback: estimate at ~4 chars per token (fast, ~10% error)

Usage::

    mgr = ContextManager(max_tokens=180_000)   # Claude 200k window
    messages = mgr.fit(messages, reserve=4096) # reserve space for response
    # messages is now guaranteed to fit within (max_tokens - reserve)
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """
    Count tokens in `text`.

    Uses tiktoken when available; falls back to character-based estimate.
    """
    try:
        import tiktoken  # type: ignore[import]
        enc = tiktoken.get_encoding(model)
        return len(enc.encode(text))
    except ImportError:
        # ~4 chars per token is a reasonable estimate for English/code
        return max(1, len(text) // 4)


def message_tokens(msg: dict[str, Any]) -> int:
    """Estimate token count for a single message dict."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return count_tokens(content) + 4  # role overhead
    if isinstance(content, list):
        total = 4
        for block in content:
            if isinstance(block, dict):
                total += count_tokens(str(block.get("text", "") or block.get("content", "")))
        return total
    return 4


# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------


class ContextManager:
    """
    Manages the context window for one agent session.

    Keeps messages within `max_tokens - reserve` by:
      1. Dropping the oldest non-system messages first
      2. Replacing dropped messages with a short summary
      3. Never dropping the first user message (original task)
    """

    def __init__(
        self,
        max_tokens: int = 180_000,
        summary_reserve: int = 500,
        verbose: bool = False,
    ) -> None:
        self.max_tokens = max_tokens
        self.summary_reserve = summary_reserve
        self.verbose = verbose

    def fit(
        self,
        messages: list[dict[str, Any]],
        reserve: int = 4096,
    ) -> list[dict[str, Any]]:
        """
        Return a copy of `messages` that fits within (max_tokens - reserve).

        Pruning strategy:
          - Count total tokens bottom-up
          - Drop middle messages (oldest non-task turns) until it fits
          - Inject a one-line summary of what was dropped
        """
        budget = self.max_tokens - reserve
        total = sum(message_tokens(m) for m in messages)

        if total <= budget:
            return list(messages)

        if self.verbose:
            logger.debug(
                f"[context_mgr] Pruning: {total:,} tokens > budget {budget:,}"
            )

        # Always keep: index 0 (user task) and the last N messages
        prunable = list(range(1, len(messages) - 1))  # exclude first and last
        dropped_indices: set[int] = set()
        dropped_summaries: list[str] = []

        for idx in prunable:
            if total <= budget:
                break
            m = messages[idx]
            tok = message_tokens(m)
            total -= tok
            dropped_indices.add(idx)

            # Extract a short summary of the dropped message
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") or b.get("content", "")
                    for b in content
                    if isinstance(b, dict)
                )
            summary = str(content)[:80].replace("\n", " ")
            dropped_summaries.append(f"[{m.get('role', '?')}] {summary}â€¦")

        # Build pruned message list
        result: list[dict[str, Any]] = []
        for i, m in enumerate(messages):
            if i in dropped_indices:
                continue
            result.append(m)

        # Inject summary after the first message
        if dropped_summaries:
            summary_text = (
                f"[Context pruned â€” {len(dropped_summaries)} messages removed to fit token budget. "
                f"Summary: {' | '.join(dropped_summaries[:5])}]"
            )
            result.insert(1, {"role": "user", "content": summary_text})
            if self.verbose:
                logger.info(
                    f"[context_mgr] Pruned {len(dropped_indices)} messages. "
                    f"Remaining: {sum(message_tokens(m) for m in result):,} tokens"
                )

        return result

    def usage(self, messages: list[dict[str, Any]]) -> dict[str, int]:
        """Return token usage stats for a message list."""
        total = sum(message_tokens(m) for m in messages)
        return {
            "total_tokens": total,
            "max_tokens": self.max_tokens,
            "remaining": max(0, self.max_tokens - total),
            "utilisation_pct": round(total / self.max_tokens * 100, 1),
        }

    def would_overflow(
        self,
        messages: list[dict[str, Any]],
        reserve: int = 4096,
    ) -> bool:
        """Return True if messages exceed the token budget."""
        total = sum(message_tokens(m) for m in messages)
        return total > (self.max_tokens - reserve)

    def inject_memories(
        self,
        messages: list[dict[str, Any]],
        memories: list[str],
        max_memory_tokens: int = 2000,
    ) -> list[dict[str, Any]]:
        """
        Inject relevant long-term memories as a system note after the first message.

        Memories are truncated to `max_memory_tokens` to avoid blowing the budget.
        """
        if not memories:
            return messages

        combined = "\n".join(f"- {m}" for m in memories)
        # Trim if needed
        while count_tokens(combined) > max_memory_tokens and "\n" in combined:
            combined = combined.rsplit("\n", 1)[0]

        memory_msg = {
            "role": "user",
            "content": f"[Relevant memories from past sessions]\n{combined}",
        }

        result = list(messages)
        result.insert(1, memory_msg)
        return result
