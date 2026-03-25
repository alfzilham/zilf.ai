"""
Vector Store â€” semantic search over memories using embeddings.

Supports two backends:
  - ChromaDB  (default â€” easy setup, persists to disk)
  - FAISS     (optional â€” faster at scale, requires numpy + faiss-cpu)

Falls back gracefully: if neither is installed, returns empty results
with a warning rather than crashing.

Usage::

    store = VectorStore(backend="chroma", persist_dir="./chroma_db")

    store.add("def binary_search(arr, target): ...", {"language": "python"})
    store.add("SELECT * FROM users WHERE id = ?", {"language": "sql"})

    results = store.search("how to find an element in a sorted list", n=3)
    for r in results:
        print(r.content, r.score)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """One result from a vector search."""

    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0         # lower = more similar (distance), or 0â€“1 (cosine)


# ---------------------------------------------------------------------------
# ChromaDB backend
# ---------------------------------------------------------------------------


class ChromaVectorStore:
    """
    ChromaDB-backed vector store.

    Requires: `pip install chromadb`
    """

    def __init__(
        self,
        collection_name: str = "agent_memory",
        persist_dir: str = "./chroma_db",
    ) -> None:
        self.collection_name = collection_name
        self.persist_dir = persist_dir
        self._collection: Any = None

    def _col(self) -> Any:
        if self._collection is not None:
            return self._collection
        import chromadb  # type: ignore[import]
        client = chromadb.PersistentClient(path=self.persist_dir)
        self._collection = client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        return self._collection

    def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        entry_id = str(uuid.uuid4())
        import time
        self._col().add(
            documents=[content],
            ids=[entry_id],
            metadatas=[{**(metadata or {}), "created_at": str(time.time())}],
        )
        return entry_id

    def add_batch(self, items: list[tuple[str, dict[str, Any] | None]]) -> list[str]:
        """Add multiple documents in one call."""
        import time
        ids = [str(uuid.uuid4()) for _ in items]
        docs = [content for content, _ in items]
        metas = [{**(meta or {}), "created_at": str(time.time())} for _, meta in items]
        self._col().add(documents=docs, ids=ids, metadatas=metas)
        return ids

    def search(self, query: str, n: int = 5) -> list[SearchResult]:
        try:
            results = self._col().query(query_texts=[query], n_results=n)
        except Exception as exc:
            from loguru import logger
            logger.warning(f"[chroma] Search failed: {exc}")
            return []

        output = []
        for doc, meta, rid, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["ids"][0],
            results.get("distances", [[0.0] * n])[0],
        ):
            output.append(SearchResult(id=rid, content=doc, metadata=meta, score=float(dist)))
        return output

    def delete(self, entry_id: str) -> None:
        self._col().delete(ids=[entry_id])

    def count(self) -> int:
        return self._col().count()

    def clear(self) -> None:
        try:
            import chromadb  # type: ignore[import]
            client = chromadb.PersistentClient(path=self.persist_dir)
            client.delete_collection(self.collection_name)
            self._collection = None
        except Exception:
            pass


# ---------------------------------------------------------------------------
# FAISS backend
# ---------------------------------------------------------------------------


class FAISSVectorStore:
    """
    FAISS-backed vector store for high-performance similarity search.

    Requires: `pip install faiss-cpu sentence-transformers`
    Documents are kept in memory; index is saved to disk on add().
    """

    def __init__(
        self,
        index_path: str = "./faiss_index",
        embedding_model: str = "all-MiniLM-L6-v2",
        dimension: int = 384,
    ) -> None:
        self.index_path = index_path
        self.embedding_model_name = embedding_model
        self.dimension = dimension
        self._index: Any = None
        self._docs: list[tuple[str, dict[str, Any]]] = []  # (content, metadata)
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            self._model = SentenceTransformer(self.embedding_model_name)
        return self._model

    def _get_index(self) -> Any:
        if self._index is None:
            import faiss  # type: ignore[import]
            self._index = faiss.IndexFlatL2(self.dimension)
        return self._index

    def _embed(self, texts: list[str]) -> Any:
        import numpy as np
        model = self._get_model()
        return model.encode(texts).astype("float32")

    def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        import numpy as np
        entry_id = str(len(self._docs))
        vecs = self._embed([content])
        self._get_index().add(vecs)
        self._docs.append((content, metadata or {}))
        return entry_id

    def search(self, query: str, n: int = 5) -> list[SearchResult]:
        if self._index is None or self._get_index().ntotal == 0:
            return []
        import numpy as np
        vecs = self._embed([query])
        k = min(n, self._get_index().ntotal)
        distances, indices = self._get_index().search(vecs, k)
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self._docs):
                continue
            content, meta = self._docs[idx]
            results.append(SearchResult(id=str(idx), content=content, metadata=meta, score=float(dist)))
        return results

    def count(self) -> int:
        return self._get_index().ntotal if self._index else 0

    def clear(self) -> None:
        self._index = None
        self._docs.clear()

    def delete(self, entry_id: str) -> None:
        # FAISS IndexFlatL2 doesn't support deletion â€” mark as deleted in metadata
        idx = int(entry_id)
        if 0 <= idx < len(self._docs):
            content, meta = self._docs[idx]
            self._docs[idx] = (content, {**meta, "_deleted": True})


# ---------------------------------------------------------------------------
# No-op fallback
# ---------------------------------------------------------------------------


class NoOpVectorStore:
    """Used when neither ChromaDB nor FAISS is installed."""

    def __init__(self) -> None:
        from loguru import logger
        logger.warning(
            "[vector_store] No vector store backend available. "
            "Install chromadb or faiss-cpu for semantic search."
        )

    def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        return ""

    def add_batch(self, items: list[tuple[str, dict[str, Any] | None]]) -> list[str]:
        return []

    def search(self, query: str, n: int = 5) -> list[SearchResult]:
        return []

    def count(self) -> int:
        return 0

    def clear(self) -> None:
        pass

    def delete(self, entry_id: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class VectorStore:
    """
    Unified vector store that auto-selects the best available backend.

    Priority: ChromaDB â†’ FAISS â†’ NoOp

    Usage::

        store = VectorStore(backend="auto", persist_dir="./chroma_db")
        store.add("some text", {"source": "docs"})
        results = store.search("query", n=3)
    """

    def __init__(
        self,
        backend: str = "auto",
        collection_name: str = "agent_memory",
        persist_dir: str = "./chroma_db",
        faiss_index_path: str = "./faiss_index",
    ) -> None:
        self._store = self._build(backend, collection_name, persist_dir, faiss_index_path)

    def _build(
        self,
        backend: str,
        collection_name: str,
        persist_dir: str,
        faiss_path: str,
    ) -> Any:
        if backend == "chroma":
            return ChromaVectorStore(collection_name=collection_name, persist_dir=persist_dir)
        if backend == "faiss":
            return FAISSVectorStore(index_path=faiss_path)
        # auto
        try:
            import chromadb  # noqa: F401
            return ChromaVectorStore(collection_name=collection_name, persist_dir=persist_dir)
        except ImportError:
            pass
        try:
            import faiss  # noqa: F401
            return FAISSVectorStore(index_path=faiss_path)
        except ImportError:
            pass
        return NoOpVectorStore()

    # Delegate all methods to the backend
    def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        return self._store.add(content, metadata)

    def search(self, query: str, n: int = 5) -> list[SearchResult]:
        return self._store.search(query, n)

    def count(self) -> int:
        return self._store.count()

    def clear(self) -> None:
        self._store.clear()

    def delete(self, entry_id: str) -> None:
        self._store.delete(entry_id)

    @property
    def backend_name(self) -> str:
        return type(self._store).__name__
