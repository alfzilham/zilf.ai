"""
Web Search Tool — searches the web for documentation, error messages, and code examples.

Provider chain:
  1. Tavily  (primary — designed for AI agents, structured results)
  2. DuckDuckGo  (fallback — free, no API key required)

Features:
  - TTL cache (1 hour) to avoid duplicate API calls
  - Rate limiting (0.5s between uncached requests)
  - Result truncation to stay within token budget
  - Direct answer injection when Tavily returns one
"""

from __future__ import annotations

import os
import time
from typing import Optional

from loguru import logger

from agent.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Simple TTL cache (avoids pulling in cachetools as a hard dep)
# ---------------------------------------------------------------------------


class _TTLCache:
    def __init__(self, maxsize: int = 100, ttl: float = 3600) -> None:
        self._store: dict[str, tuple[float, object]] = {}
        self.maxsize = maxsize
        self.ttl = ttl

    def get(self, key: str) -> object | None:
        if key in self._store:
            ts, val = self._store[key]
            if time.time() - ts < self.ttl:
                return val
            del self._store[key]
        return None

    def set(self, key: str, value: object) -> None:
        if len(self._store) >= self.maxsize:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest]
        self._store[key] = (time.time(), value)


_cache = _TTLCache(maxsize=100, ttl=3600)
_last_request_time: float = 0.0
_RATE_LIMIT_DELAY = 0.5   # seconds between uncached requests

MAX_RESULT_CHARS = 400    # per result snippet
MAX_RESULTS = 10


# ---------------------------------------------------------------------------
# Provider: Tavily
# ---------------------------------------------------------------------------


async def _tavily_search(query: str, max_results: int, search_depth: str) -> list[dict] | None:
    """Return structured results from Tavily, or None if unavailable."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return None

    try:
        from tavily import TavilyClient  # type: ignore[import]
    except ImportError:
        logger.debug("[web_search] tavily-python not installed, skipping.")
        return None

    try:
        client = TavilyClient(api_key=api_key)
        resp = client.search(
            query=query,
            max_results=max_results,
            search_depth=search_depth,
            include_answer=True,
            include_raw_content=False,
        )
        results: list[dict] = []

        # Inject direct answer as the first result if present
        if resp.get("answer"):
            results.append({
                "title": "Direct answer",
                "url": "",
                "snippet": resp["answer"],
                "score": 1.0,
            })

        for r in resp.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", "")[:MAX_RESULT_CHARS],
                "score": r.get("score", 0.0),
            })

        return results

    except Exception as exc:
        logger.warning(f"[web_search] Tavily error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Provider: DuckDuckGo
# ---------------------------------------------------------------------------

def _ensure_ddg_installed() -> None:
    try:
        import duckduckgo_search  # noqa
    except ImportError:
        import subprocess
        import sys
        logger.info("[web_search] duckduckgo-search not found, attempting auto-install...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "duckduckgo-search", "--quiet"])

async def _ddg_search(query: str, max_results: int) -> list[dict]:
    """Return results from DuckDuckGo. No API key required."""
    _ensure_ddg_installed()
    from duckduckgo_search import DDGS  # type: ignore[import]

    try:
        results: list[dict] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", "")[:MAX_RESULT_CHARS],
                    "score": 0.0,
                })
        return results
    except Exception as exc:
        logger.warning(f"[web_search] DuckDuckGo error: {exc}")
        return [{"title": "Search failed", "url": "", "snippet": str(exc), "score": 0.0}]


# ---------------------------------------------------------------------------
# Format results
# ---------------------------------------------------------------------------


def _format_results(results: list[dict], query: str) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        if r["url"]:
            lines.append(f"    URL: {r['url']}")
        if r["snippet"]:
            lines.append(f"    {r['snippet']}")
        lines.append("")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------


async def web_search(
    query: str,
    max_results: Optional[int] = 5,
    search_depth: Optional[str] = "basic",
) -> str:
    """
    Search the web and return titles, URLs, and snippets.

    Use this when you need library documentation, to look up error messages,
    find recent API changes, or research a topic not in the workspace.
    Do NOT use this to find files in the workspace — use list_dir or search_files instead.

    Args:
        query: Search query. Be specific — include library names, version numbers,
            and verbatim error text. Example: 'python httpx AsyncClient timeout 0.27'
        max_results: Number of results to return. Default 5, max 10.
        search_depth: 'basic' (faster) or 'advanced' (more thorough, Tavily only).
    """
    global _last_request_time

    n = min(int(max_results or 5), MAX_RESULTS)
    depth = search_depth if search_depth in ("basic", "advanced") else "basic"

    # Cache lookup
    cache_key = f"{query}:{n}:{depth}"
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"[web_search] Cache hit: {query!r}")
        return str(cached)

    # Rate limiting
    elapsed = time.time() - _last_request_time
    if elapsed < _RATE_LIMIT_DELAY:
        import asyncio
        await asyncio.sleep(_RATE_LIMIT_DELAY - elapsed)
    _last_request_time = time.time()

    logger.info(f"[web_search] Searching: {query!r} (n={n}, depth={depth})")

    # Try Tavily first, fall back to DuckDuckGo
    results = await _tavily_search(query, n, depth)
    source = "Tavily"
    if results is None:
        results = await _ddg_search(query, n)
        source = "DuckDuckGo"

    logger.debug(f"[web_search] {source}: {len(results)} results for {query!r}")

    formatted = _format_results(results, query)
    _cache.set(cache_key, formatted)
    return formatted


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_web_search_tools(registry: ToolRegistry) -> None:
    """Register web search tools into the given registry."""
    registry.tool(web_search)
