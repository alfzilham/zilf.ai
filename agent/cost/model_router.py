"""
Model Router â€” cost-aware model selection and prompt optimization utilities.

Components:
  ModelRouter          â€” selects cheap/medium/expensive model by task complexity
  FileChunker          â€” splits large Python files by function/class (AST-based)
  PromptCompressor     â€” removes whitespace and common verbose phrases

Model tier routing (from Token & API Cost Management.md):

  LOW complexity   â†’ haiku / flash  (typo fix, doc update, rename)
  MEDIUM complexity â†’ sonnet / gpt-4o-mini  (feature, bug fix)
  HIGH complexity  â†’ opus / gpt-4o  (architecture, algorithm design)

Usage::

    router = ModelRouter()
    model = router.select("Design a microservices architecture")
    # â†’ "claude-opus-4"

    chunker = FileChunker()
    chunks = chunker.chunk_file("src/parser.py")
    relevant = chunker.filter_relevant(chunks, "tokenize function")

    compressed = PromptCompressor().compress(long_prompt)
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------


# Keywords that hint at task complexity
_LOW_KEYWORDS: tuple[str, ...] = (
    "fix typo", "update doc", "rename variable", "add comment",
    "format", "whitespace", "indent", "spelling", "grammar",
)

_HIGH_KEYWORDS: tuple[str, ...] = (
    "design", "architect", "architecture", "optimize", "refactor",
    "algorithm", "data structure", "system design", "migrate",
    "security audit", "performance", "scalab",
)

# Model tiers â€” override via ModelRouter(tiers={...})
DEFAULT_TIERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "low":    "claude-haiku-4-5",
        "medium": "claude-sonnet-4-5",
        "high":   "claude-opus-4",
    },
    "openai": {
        "low":    "gpt-4o-mini",
        "medium": "gpt-4o",
        "high":   "gpt-4o",
    },
    "ollama": {
        "low":    "llama3",
        "medium": "llama3",
        "high":   "llama3",
    },
}


class ModelRouter:
    """
    Selects the appropriate model tier based on task complexity.

    Rules (from Token & API Cost Management.md):
      LOW    â†’ cheap model (haiku / flash)
      MEDIUM â†’ balanced model (sonnet / gpt-4o)
      HIGH   â†’ most capable model (opus / gpt-4o)
    """

    def __init__(
        self,
        provider: str = "anthropic",
        tiers: dict[str, str] | None = None,
    ) -> None:
        self.provider = provider
        _defaults = DEFAULT_TIERS.get(provider, DEFAULT_TIERS["anthropic"])
        self._tiers = tiers or _defaults

    def select(self, task_description: str) -> str:
        """
        Return the model name appropriate for `task_description`.

        Args:
            task_description: Natural-language task string.

        Returns:
            Model name string (e.g. "claude-sonnet-4-5").
        """
        lower = task_description.lower()

        if any(kw in lower for kw in _HIGH_KEYWORDS):
            return self._tiers["high"]
        if any(kw in lower for kw in _LOW_KEYWORDS):
            return self._tiers["low"]
        return self._tiers["medium"]

    def complexity(self, task_description: str) -> str:
        """Return "low" | "medium" | "high" for a task description."""
        lower = task_description.lower()
        if any(kw in lower for kw in _HIGH_KEYWORDS):
            return "high"
        if any(kw in lower for kw in _LOW_KEYWORDS):
            return "low"
        return "medium"


# ---------------------------------------------------------------------------
# FileChunker
# ---------------------------------------------------------------------------


@dataclass
class FileChunk:
    """One chunk of a source file."""
    name: str
    content: str
    start_line: int
    end_line: int
    chunk_type: str = "function"   # function | class | module

    def __str__(self) -> str:
        return f"[{self.chunk_type}:{self.name} L{self.start_line}-{self.end_line}]"


class FileChunker:
    """
    Splits large Python source files into function/class-level chunks.

    Uses the `ast` module for accurate boundary detection.
    Falls back to line-based splitting when the file is not valid Python.

    Usage::

        chunker = FileChunker()
        chunks = chunker.chunk_file("src/parser.py")
        relevant = chunker.filter_relevant(chunks, "tokenize function")
    """

    def chunk_file(
        self,
        file_path: str | Path,
        max_lines_per_chunk: int = 80,
    ) -> list[FileChunk]:
        """
        Chunk a Python source file by top-level function/class definitions.

        Args:
            file_path:           Path to the .py file.
            max_lines_per_chunk: Fallback chunk size for non-Python files.

        Returns:
            List of FileChunk objects, each representing one definition.
        """
        path = Path(file_path)
        if not path.exists():
            return []

        source = path.read_text(encoding="utf-8", errors="replace")

        if path.suffix == ".py":
            return self._chunk_python(source, path.name)
        else:
            return self._chunk_by_lines(source, path.name, max_lines_per_chunk)

    def chunk_text(
        self,
        source: str,
        filename: str = "<string>",
    ) -> list[FileChunk]:
        """Chunk a Python source string."""
        return self._chunk_python(source, filename)

    def filter_relevant(
        self,
        chunks: list[FileChunk],
        query: str,
        max_chunks: int = 5,
    ) -> list[FileChunk]:
        """
        Return the top-k chunks most relevant to `query` (keyword scoring).

        Simple but fast â€” no embeddings needed.
        """
        query_words = {w.lower() for w in re.split(r"\W+", query) if len(w) > 2}
        if not query_words:
            return chunks[:max_chunks]

        scored: list[tuple[int, FileChunk]] = []
        for chunk in chunks:
            haystack = (chunk.name + " " + chunk.content).lower()
            score = sum(1 for w in query_words if w in haystack)
            scored.append((score, chunk))

        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:max_chunks] if _ > 0] or chunks[:max_chunks]

    # â”€â”€ Private â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _chunk_python(self, source: str, filename: str) -> list[FileChunk]:
        try:
            tree  = ast.parse(source)
            lines = source.splitlines()
            chunks: list[FileChunk] = []

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if not hasattr(node, "lineno"):
                    continue

                start = node.lineno - 1
                end   = getattr(node, "end_lineno", node.lineno) - 1
                chunk_lines = lines[start : end + 1]

                chunks.append(FileChunk(
                    name       = node.name,
                    content    = "\n".join(chunk_lines),
                    start_line = start + 1,
                    end_line   = end + 1,
                    chunk_type = "class" if isinstance(node, ast.ClassDef) else "function",
                ))

            if not chunks:
                # No top-level defs â€” treat whole file as one chunk
                chunks.append(FileChunk(
                    name       = filename,
                    content    = source,
                    start_line = 1,
                    end_line   = len(lines),
                    chunk_type = "module",
                ))

            # Sort by line number
            chunks.sort(key=lambda c: c.start_line)
            return chunks

        except SyntaxError:
            return self._chunk_by_lines(source, filename, 60)

    def _chunk_by_lines(
        self,
        source: str,
        filename: str,
        chunk_size: int,
    ) -> list[FileChunk]:
        """Fallback: split by fixed line count."""
        lines   = source.splitlines()
        chunks  = []
        for i in range(0, len(lines), chunk_size):
            block = lines[i : i + chunk_size]
            chunks.append(FileChunk(
                name       = f"{filename}[{i+1}-{i+len(block)}]",
                content    = "\n".join(block),
                start_line = i + 1,
                end_line   = i + len(block),
                chunk_type = "module",
            ))
        return chunks


# ---------------------------------------------------------------------------
# PromptCompressor
# ---------------------------------------------------------------------------

_VERBOSE_PHRASES: list[tuple[str, str]] = [
    ("the user asked",        "user asked"),
    ("please note that",      "note:"),
    ("it is important to",    "important:"),
    ("in order to",           "to"),
    ("please be advised",     "note:"),
    ("as previously mentioned", "previously,"),
    ("it should be noted",    "note:"),
    ("at this point in time", "now"),
    ("due to the fact that",  "because"),
    ("in the event that",     "if"),
    ("with regard to",        "regarding"),
    ("on a regular basis",    "regularly"),
    ("for the purpose of",    "for"),
    ("a large number of",     "many"),
    ("at the present time",   "currently"),
]

_WS_RE = re.compile(r"[ \t]{2,}")


class PromptCompressor:
    """
    Lightweight prompt compressor.

    Applies two transformations:
      1. Collapses multiple consecutive spaces/tabs to one
      2. Replaces verbose multi-word phrases with concise equivalents

    This is NOT semantic compression â€” it is a fast preprocessing step.
    For heavy compression, use SummarizationWindow instead.
    """

    def compress(self, text: str) -> str:
        """Return a compressed copy of `text`."""
        result = _WS_RE.sub(" ", text)
        lower  = result.lower()
        for long_phrase, short_phrase in _VERBOSE_PHRASES:
            if long_phrase in lower:
                # Case-insensitive replace preserving original casing position
                result = re.sub(
                    re.escape(long_phrase),
                    short_phrase,
                    result,
                    flags=re.IGNORECASE,
                )
                lower = result.lower()
        return result.strip()

    def compression_ratio(self, original: str, compressed: str) -> float:
        """Return ratio of compressed length to original length."""
        if not original:
            return 1.0
        return round(len(compressed) / len(original), 3)
