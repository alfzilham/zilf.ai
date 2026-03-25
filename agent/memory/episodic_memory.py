"""
Episodic Memory â€” stores complete past task run records for retrieval and replay.

Each episode captures:
  - The original task description
  - The sequence of actions taken
  - The final outcome and reward
  - Timing and token metadata

Episodes are persisted to disk as a JSON-lines file so they survive restarts.
Retrieval is by simple substring/keyword match (no embeddings required).
For semantic retrieval, use VectorStore instead.

Usage::

    mem = EpisodicMemory(storage_path="./memory")

    mem.add_episode(
        task="Fix the null pointer in auth.py",
        actions=[{"tool": "read_file", "path": "auth.py"}, ...],
        outcome="Fixed by adding null check on line 42",
        reward=1.0,
    )

    similar = mem.search("authentication error")
    best    = mem.get_successful_episodes(min_reward=0.8)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Episode data class
# ---------------------------------------------------------------------------


@dataclass
class Episode:
    """One complete record of a task run."""

    episode_id: str
    task: str
    actions: list[dict[str, Any]]
    outcome: str
    reward: float                      # 0.0 = failure, 1.0 = perfect success
    input_tokens: int = 0
    output_tokens: int = 0
    steps_taken: int = 0
    elapsed_seconds: float = 0.0
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def success(self) -> bool:
        return self.reward >= 0.5

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_summary(self) -> str:
        icon = "âœ“" if self.success else "âœ—"
        return (
            f"{icon} [{self.episode_id[:8]}] {self.task[:80]} "
            f"| reward={self.reward:.2f} steps={self.steps_taken}"
        )


# ---------------------------------------------------------------------------
# Episodic Memory
# ---------------------------------------------------------------------------


class EpisodicMemory:
    """
    Append-only episodic memory backed by a JSON-lines file.

    In-memory index allows fast search; file provides persistence.
    """

    def __init__(
        self,
        storage_path: str = "./memory",
        max_episodes: int = 1000,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.max_episodes = max_episodes
        self._episodes: deque[Episode] = deque(maxlen=max_episodes)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._load()

    # -----------------------------------------------------------------------
    # Write
    # -----------------------------------------------------------------------

    def add_episode(
        self,
        task: str,
        actions: list[dict[str, Any]],
        outcome: str,
        reward: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        steps_taken: int = 0,
        elapsed_seconds: float = 0.0,
        tags: list[str] | None = None,
    ) -> Episode:
        """
        Record a completed task run.

        Args:
            task:             Original task description.
            actions:          List of tool calls made during the run.
            outcome:          Human-readable description of what happened.
            reward:           Success score 0.0â€“1.0.
            input_tokens:     Total input tokens consumed.
            output_tokens:    Total output tokens generated.
            steps_taken:      Number of reasoning steps.
            elapsed_seconds:  Wall-clock time for the run.
            tags:             Optional labels (e.g. ["bug_fix", "python"]).
        """
        ep = Episode(
            episode_id=str(uuid.uuid4()),
            task=task,
            actions=actions,
            outcome=outcome,
            reward=reward,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            steps_taken=steps_taken,
            elapsed_seconds=elapsed_seconds,
            tags=tags or [],
        )
        self._episodes.append(ep)
        self._append_to_disk(ep)
        return ep

    @classmethod
    def from_agent_response(
        cls,
        instance: "EpisodicMemory",
        task: str,
        response: Any,
        elapsed_seconds: float = 0.0,
        tags: list[str] | None = None,
    ) -> Episode:
        """
        Convenience: build an Episode directly from an AgentResponse object.
        """
        reward = 1.0 if getattr(response, "success", False) else 0.0
        actions = [
            {"step": s.step_number, "tools": [tc.tool_name for tc in s.tool_calls]}
            for s in getattr(response, "_state", None) and response._state.steps or []
        ]
        return instance.add_episode(
            task=task,
            actions=actions,
            outcome=response.final_answer or response.error or "",
            reward=reward,
            input_tokens=getattr(response, "total_input_tokens", 0),
            output_tokens=getattr(response, "total_output_tokens", 0),
            steps_taken=getattr(response, "steps_taken", 0),
            elapsed_seconds=elapsed_seconds,
            tags=tags or [],
        )

    # -----------------------------------------------------------------------
    # Read
    # -----------------------------------------------------------------------

    def get_recent(self, n: int = 10) -> list[Episode]:
        """Return the N most recent episodes."""
        episodes = list(self._episodes)
        return episodes[-n:] if n < len(episodes) else episodes

    def get_successful_episodes(self, min_reward: float = 0.5) -> list[Episode]:
        """Return all episodes with reward >= min_reward."""
        return [ep for ep in self._episodes if ep.reward >= min_reward]

    def search(self, query: str, n: int = 5) -> list[Episode]:
        """
        Keyword search over task descriptions and outcomes.

        Scores each episode by the number of query words that appear in
        the task or outcome text (case-insensitive). Returns top-n by score.
        """
        words = [w.lower() for w in query.split() if len(w) > 2]
        if not words:
            return self.get_recent(n)

        scored: list[tuple[int, Episode]] = []
        for ep in self._episodes:
            haystack = (ep.task + " " + ep.outcome).lower()
            score = sum(1 for w in words if w in haystack)
            if score > 0:
                scored.append((score, ep))

        scored.sort(key=lambda x: (-x[0], -x[1].reward))
        return [ep for _, ep in scored[:n]]

    def search_by_tag(self, tag: str) -> list[Episode]:
        """Return all episodes that have the given tag."""
        return [ep for ep in self._episodes if tag in ep.tags]

    def stats(self) -> dict[str, Any]:
        """Return summary statistics."""
        episodes = list(self._episodes)
        if not episodes:
            return {"total": 0}
        rewards = [ep.reward for ep in episodes]
        return {
            "total": len(episodes),
            "successful": sum(1 for ep in episodes if ep.success),
            "success_rate": round(sum(rewards) / len(rewards) * 100, 1),
            "avg_steps": round(sum(ep.steps_taken for ep in episodes) / len(episodes), 1),
            "avg_tokens": round(sum(ep.total_tokens for ep in episodes) / len(episodes)),
        }

    def clear(self) -> None:
        """Clear all in-memory episodes (does not delete the disk file)."""
        self._episodes.clear()

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def _file_path(self) -> Path:
        return self.storage_path / "episodes.jsonl"

    def _append_to_disk(self, ep: Episode) -> None:
        try:
            with self._file_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(ep)) + "\n")
        except Exception as exc:
            from loguru import logger
            logger.warning(f"[episodic] Could not write to disk: {exc}")

    def _load(self) -> None:
        """Load episodes from the JSONL file on startup."""
        path = self._file_path()
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            for line in lines[-self.max_episodes:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    self._episodes.append(Episode(**data))
                except Exception:
                    pass
        except Exception as exc:
            from loguru import logger
            logger.warning(f"[episodic] Could not load episodes: {exc}")
