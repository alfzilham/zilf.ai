"""
Memory System â€” short-term context + long-term vector store.

Three memory tiers:
  - Short-term  : the current context window (recent steps, task description)
  - Working     : facts extracted from the current step's observations
  - Long-term   : ChromaDB-backed semantic store for cross-run recall

The MemoryManager is the single interface used by Agent and ReasoningLoop.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loguru import logger

from agent.core.state import MemoryEntry


# ---------------------------------------------------------------------------
# Short-term memory (in-process, cleared per run)
# ---------------------------------------------------------------------------


class ShortTermMemory:
    """
    Stores the most recent N entries in a simple ring buffer.
    Used to keep the last few observations visible in the LLM context
    without overflowing the token budget.
    """

    def __init__(self, max_entries: int = 50) -> None:
        self._entries: list[MemoryEntry] = []
        self.max_entries = max_entries

    def add(self, content: str, metadata: dict[str, Any] | None = None) -> MemoryEntry:
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            content=content,
            metadata=metadata or {},
            memory_type="short_term",
        )
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries.pop(0)
        return entry

    def recent(self, n: int = 10) -> list[MemoryEntry]:
        return self._entries[-n:]

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Long-term memory (ChromaDB â€” lazy import so tests don't require it)
# ---------------------------------------------------------------------------


class LongTermMemory:
    """
    Semantic memory backed by ChromaDB.

    Stores summaries of past task runs, code snippets, and learned patterns.
    Supports similarity search so the agent can recall relevant context
    from previous runs.
    """

    def __init__(self, collection_name: str = "agent_memory", persist_dir: str = "./chroma_db") -> None:
        self.collection_name = collection_name
        self.persist_dir = persist_dir
        self._collection: Any = None  # lazy-init

    def _get_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        try:
            import chromadb  # type: ignore[import]

            client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except ImportError:
            logger.warning("chromadb not installed â€” long-term memory disabled.")
            self._collection = _NoOpCollection()
        return self._collection

    def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        entry_id = str(uuid.uuid4())
        col = self._get_collection()
        col.add(
            documents=[content],
            ids=[entry_id],
            metadatas=[{**(metadata or {}), "created_at": datetime.utcnow().isoformat()}],
        )
        return entry_id

    def search(self, query: str, n_results: int = 5) -> list[MemoryEntry]:
        col = self._get_collection()
        try:
            results = col.query(query_texts=[query], n_results=n_results)
        except Exception as exc:
            logger.warning(f"Long-term memory search failed: {exc}")
            return []

        entries = []
        for doc, meta, entry_id in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["ids"][0],
        ):
            entries.append(
                MemoryEntry(id=entry_id, content=doc, metadata=meta, memory_type="long_term")
            )
        return entries

    def clear(self) -> None:
        try:
            import chromadb  # type: ignore[import]

            client = chromadb.PersistentClient(path=self.persist_dir)
            client.delete_collection(self.collection_name)
            self._collection = None
        except Exception:
            pass


class _NoOpCollection:
    """Fallback when ChromaDB is not installed."""

    def add(self, **kwargs: Any) -> None:  # noqa: ANN401
        pass

    def query(self, **kwargs: Any) -> dict[str, list]:  # noqa: ANN401
        return {"documents": [[]], "metadatas": [[]], "ids": [[]]}


# ---------------------------------------------------------------------------
# Unified MemoryManager
# ---------------------------------------------------------------------------


class MemoryManager:
    """
    Facade over short-term and long-term memory.

    The reasoning loop calls `observe()` after each step to log observations,
    and `recall()` to inject relevant past context before each LLM call.
    """

    def __init__(
        self,
        max_short_term: int = 50,
        long_term_persist_dir: str = "./chroma_db",
        enable_long_term: bool = True,
    ) -> None:
        self.short_term = ShortTermMemory(max_entries=max_short_term)
        self.long_term = LongTermMemory(persist_dir=long_term_persist_dir) if enable_long_term else None

    def observe(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Store an observation in short-term memory."""
        self.short_term.add(content, metadata)

    def memorize(self, content: str, metadata: dict[str, Any] | None = None) -> str | None:
        """Persist important information to long-term memory."""
        if self.long_term:
            return self.long_term.add(content, metadata)
        return None

    def recall(self, query: str, n: int = 3) -> list[MemoryEntry]:
        """
        Retrieve relevant entries from long-term memory by semantic similarity.
        Falls back to recent short-term entries if long-term is unavailable.
        """
        if self.long_term:
            results = self.long_term.search(query, n_results=n)
            if results:
                return results
        return self.short_term.recent(n)

    def recent_observations(self, n: int = 5) -> list[MemoryEntry]:
        return self.short_term.recent(n)

    def clear_session(self) -> None:
        """Clear short-term memory at the end of a run."""
        self.short_term.clear()
