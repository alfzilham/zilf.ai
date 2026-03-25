"""
Windowing Strategies â€” keep the LLM context within budget across long sessions.

Four strategies (from simplest to most sophisticated):

  1. SlidingWindow        â€” drop oldest messages; fast, loses early context
  2. SummarizationWindow  â€” compress old turns via LLM call; preserves semantics
  3. RetrievalWindow      â€” embed all turns, retrieve by cosine similarity
  4. HierarchicalContext  â€” three tiers: verbatim â†’ summary â†’ key-fact archive

Recommended default for production: HierarchicalContext.

Usage::

    # Sliding (no LLM needed)
    sw = SlidingWindow(max_tokens=6_000)
    messages = sw.apply(messages)

    # Summarization (requires an LLM)
    sumw = SummarizationWindow(llm=my_llm, max_tokens=6_000)
    messages = await sumw.apply(messages)

    # Hierarchical (recommended)
    hc = HierarchicalContext(llm=my_llm)
    hc.add({"role": "user", "content": "..."})
    hc.add({"role": "assistant", "content": "..."})
    messages = hc.build_context()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.cost.context_budget import (
    BUDGET,
    count_message_tokens,
    count_tokens,
)

Message = dict[str, Any]


# ---------------------------------------------------------------------------
# 1. Sliding Window
# ---------------------------------------------------------------------------


class SlidingWindow:
    """
    Keeps the most recent messages within `max_tokens`.

    Always preserves system messages.
    Drops oldest non-system messages until budget is met.

    Best for: short tasks where early context will not be needed again.
    Risk:     loses early decisions and user constraints.
    """

    def __init__(
        self,
        max_tokens: int = BUDGET["history"],
        keep_system: bool = True,
    ) -> None:
        self.max_tokens = max_tokens
        self.keep_system = keep_system

    def apply(self, messages: list[Message]) -> list[Message]:
        """Return a trimmed copy of `messages` that fits within max_tokens."""
        system = [m for m in messages if m["role"] == "system"] if self.keep_system else []
        non_system = [m for m in messages if m["role"] != "system"]

        while non_system and count_message_tokens(system + non_system) > self.max_tokens:
            non_system.pop(0)

        return system + non_system

    def fits(self, messages: list[Message]) -> bool:
        return count_message_tokens(messages) <= self.max_tokens


# ---------------------------------------------------------------------------
# 2. Summarization Window
# ---------------------------------------------------------------------------


_SUMMARIZE_SYSTEM = (
    "You are a coding agent memory compressor. "
    "Given a block of conversation turns, produce a concise bullet-point summary "
    "preserving: decisions made, files changed, errors encountered, constraints stated. "
    "Output ONLY the summary â€” no preamble or closing remarks."
)


class SummarizationWindow:
    """
    When history exceeds `trigger_ratio * max_tokens`, compresses the oldest
    half of non-system turns into a single [COMPRESSED HISTORY SUMMARY] message.

    Requires an LLM with a `generate_text()` async method.

    Best for: long sessions where early decisions must remain accessible.
    """

    def __init__(
        self,
        llm: Any,
        max_tokens: int = BUDGET["history"],
        trigger_ratio: float = 0.80,
        summary_max_tokens: int = 500,
    ) -> None:
        self.llm = llm
        self.max_tokens = max_tokens
        self.trigger_ratio = trigger_ratio
        self.summary_max_tokens = summary_max_tokens

    async def apply(self, messages: list[Message]) -> list[Message]:
        """
        Return a (possibly compressed) message list.

        If total tokens < trigger, returns messages unchanged.
        Otherwise, compresses the oldest half of non-system turns.
        """
        trigger = int(self.max_tokens * self.trigger_ratio)
        current = count_message_tokens(messages)

        if current <= trigger:
            return messages

        system_msgs = [m for m in messages if m["role"] == "system"]
        non_system  = [m for m in messages if m["role"] != "system"]

        split       = max(1, len(non_system) // 2)
        to_compress = non_system[:split]
        to_keep     = non_system[split:]

        summary_text = await self._summarize(to_compress)
        summary_msg: Message = {
            "role":    "system",
            "content": f"[COMPRESSED HISTORY SUMMARY]\n{summary_text}",
        }

        from loguru import logger
        logger.debug(
            f"[windowing] Summarization: compressed {split} turns "
            f"({count_message_tokens(to_compress):,} tokens) â†’ "
            f"~{count_tokens(summary_text):,} tokens"
        )

        return system_msgs + [summary_msg] + to_keep

    async def _summarize(self, turns: list[Message]) -> str:
        formatted = "\n".join(
            f"[{m['role'].upper()}]: {m.get('content', '')}" for m in turns
        )
        try:
            return await self.llm.generate_text(
                messages=[{"role": "user", "content": formatted}],
                system=_SUMMARIZE_SYSTEM,
                max_tokens=self.summary_max_tokens,
            )
        except Exception as exc:
            from loguru import logger
            logger.warning(f"[windowing] Summarization LLM call failed: {exc}")
            # Fallback: extract first line from each turn
            lines = [m.get("content", "")[:80] for m in turns[:5]]
            return "Summary unavailable. Key turns: " + " | ".join(lines)


# ---------------------------------------------------------------------------
# 3. Retrieval Window
# ---------------------------------------------------------------------------


class RetrievalWindow:
    """
    Stores all past turns with embeddings; returns the top-k most semantically
    relevant turns for a given query, capped by token budget.

    Falls back to recent turns when no embeddings are available.

    Best for: very long sessions (100+ turns) with many unrelated sub-tasks.
    """

    def __init__(self, max_tokens: int = BUDGET["history"]) -> None:
        self.max_tokens = max_tokens
        self._turns:      list[Message] = []
        self._embeddings: list[Any] = []

    def add(self, message: Message) -> None:
        """Store a message and compute its embedding (if numpy available)."""
        self._turns.append(message)
        self._embeddings.append(self._embed(message.get("content", "")))

    def retrieve(self, query: str, top_k: int = 10) -> list[Message]:
        """
        Return up to top_k turns most similar to `query`,
        capped by the token budget and re-sorted chronologically.
        """
        if not self._turns:
            return []

        q_vec = self._embed(query)
        if q_vec is None:
            # Fallback: return most recent turns
            return self._recent(top_k)

        scores = [self._cosine(q_vec, e) for e in self._embeddings]
        ranked = sorted(range(len(self._turns)), key=lambda i: scores[i], reverse=True)

        selected: list[Message] = []
        used = 0
        for idx in ranked[:top_k]:
            turn   = self._turns[idx]
            tokens = count_message_tokens([turn])
            if used + tokens > self.max_tokens:
                break
            selected.append(turn)
            used += tokens

        # Re-sort by original insertion order for coherence
        order = {id(m): i for i, m in enumerate(self._turns)}
        selected.sort(key=lambda m: order[id(m)])
        return selected

    def _recent(self, n: int) -> list[Message]:
        recent = list(self._turns[-n:])
        while count_message_tokens(recent) > self.max_tokens:
            recent.pop(0)
        return recent

    def _embed(self, text: str) -> Any:
        try:
            import numpy as np  # type: ignore[import]
            # In production: call your embedding API here.
            # Fallback: deterministic pseudo-random vector from text hash.
            seed = abs(hash(text[:200])) % (2**31)
            rng  = np.random.default_rng(seed)
            vec  = rng.random(512).astype(np.float32)
            norm = np.linalg.norm(vec)
            return vec / norm if norm > 0 else vec
        except ImportError:
            return None

    def _cosine(self, a: Any, b: Any) -> float:
        if a is None or b is None:
            return 0.0
        try:
            import numpy as np  # type: ignore[import]
            return float(np.dot(a, b))
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# 4. Hierarchical Context (recommended default)
# ---------------------------------------------------------------------------


@dataclass
class HierarchicalContext:
    """
    Three-tier context â€” mirrors human memory:

      Tier 1 (recent):   Last `recent_turns` non-system turns, verbatim
      Tier 2 (middle):   Turns beyond recent, compressed into summaries
      Tier 3 (archive):  One-line key-fact bullets from the oldest turns

    Usage::

        hc = HierarchicalContext(llm=my_llm)
        for msg in conversation:
            hc.add(msg)
        messages_for_llm = hc.build_context()
    """

    llm: Any = field(default=None)
    recent_turns: int = 6
    middle_turns: int = 20
    max_tokens: int = BUDGET["history"]

    _turns:     list[Message] = field(default_factory=list, repr=False)
    _summaries: list[str]     = field(default_factory=list, repr=False)
    _archive:   list[str]     = field(default_factory=list, repr=False)

    def add(self, message: Message) -> None:
        """Append a message and promote overflow to lower tiers synchronously."""
        self._turns.append(message)
        self._promote_sync()

    async def add_async(self, message: Message) -> None:
        """Append a message and promote overflow using async summarization."""
        self._turns.append(message)
        await self._promote_async()

    def build_context(self) -> list[Message]:
        """
        Assemble the final message list:
          system messages â†’ archive injection â†’ summaries â†’ recent turns
        """
        system = [m for m in self._turns if m["role"] == "system"]
        recent = [m for m in self._turns if m["role"] != "system"]

        result: list[Message] = list(system)

        if self._archive:
            result.append({
                "role":    "system",
                "content": "**Archive (key facts from earlier in session):**\n"
                           + "\n".join(self._archive),
            })

        if self._summaries:
            result.append({
                "role":    "system",
                "content": "**Compressed history:**\n---\n"
                           + "\n---\n".join(self._summaries),
            })

        result.extend(recent)
        return result

    # â”€â”€ Private promotion logic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _promote_sync(self) -> None:
        """Move overflow turns to archive without LLM call."""
        non_sys = [m for m in self._turns if m["role"] != "system"]

        while len(non_sys) > self.recent_turns + self.middle_turns:
            pair        = non_sys[:2]
            key_fact    = self._key_fact(pair)
            self._archive.append(key_fact)
            for m in pair:
                self._turns.remove(m)
            non_sys = non_sys[2:]

    async def _promote_async(self) -> None:
        """Move overflow turns to summaries using LLM (better fidelity)."""
        non_sys = [m for m in self._turns if m["role"] != "system"]

        # Archive anything beyond middle window
        while len(non_sys) > self.recent_turns + self.middle_turns:
            pair     = non_sys[:2]
            key_fact = self._key_fact(pair)
            self._archive.append(key_fact)
            for m in pair:
                self._turns.remove(m)
            non_sys = non_sys[2:]

        # Summarise turns that fall outside the recent window
        overflow = len(non_sys) - self.recent_turns
        if overflow > 0 and self.llm is not None:
            to_compress = non_sys[:overflow]
            if count_message_tokens(to_compress) > 600:
                sumw = SummarizationWindow(self.llm, max_tokens=self.max_tokens)
                summary = await sumw._summarize(to_compress)
                self._summaries.append(summary)
                for m in to_compress:
                    if m in self._turns:
                        self._turns.remove(m)

    def _key_fact(self, turns: list[Message]) -> str:
        combined = " ".join(m.get("content", "")[:150] for m in turns)
        return f"â€¢ {combined[:130]}â€¦"
