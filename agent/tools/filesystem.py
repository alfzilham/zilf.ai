"""
Filesystem Tools â€” read, write, list, search, and delete files in /workspace.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Optional

from loguru import logger

from agent.tools.registry import ToolRegistry


def _resolve_workspace() -> Path:
    env_ws = os.environ.get("AGENT_WORKSPACE")
    if env_ws:
        p = Path(env_ws).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    system_ws = Path("/workspace")
    if system_ws.exists() and system_ws.is_dir():
        return system_ws
    p = Path("./workspace").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


WORKSPACE_ROOT = _resolve_workspace()

_WORKSPACE_ALIASES: list[Path] = [
    Path("/workspace"),
    WORKSPACE_ROOT,
]

MAX_READ_BYTES     = 1_000_000
MAX_SEARCH_RESULTS = 200

logger.info(f"[fs] WORKSPACE_ROOT = {WORKSPACE_ROOT}")


def _safe_path(raw: str) -> Path:
    p = Path(raw)
    for alias in _WORKSPACE_ALIASES:
        if alias == Path("/workspace") and alias != WORKSPACE_ROOT:
            try:
                rel = p.relative_to(alias)
                p = WORKSPACE_ROOT / rel
                break
            except ValueError:
                pass
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p
    resolved = p.resolve()
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    if not str(resolved).startswith(str(WORKSPACE_ROOT)):
        raise ValueError(
            f"Path '{raw}' resolves to '{resolved}' which is outside the "
            f"allowed workspace '{WORKSPACE_ROOT}'."
        )
    return resolved


def get_workspace_root() -> Path:
    return WORKSPACE_ROOT


async def read_file(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:
    """
    Read the text content of a file in /workspace.

    Args:
        path: Path to the file (absolute /workspace/... or relative).
        start_line: Optional first line to return (1-indexed).
        end_line: Optional last line to return (1-indexed, inclusive).
    """
    try:
        resolved = _safe_path(path)
    except ValueError as exc:
        return f"Error: {exc}"

    if not resolved.exists():
        return f"Error: file not found: {path}"
    if not resolved.is_file():
        return f"Error: path is a directory, not a file: {path}"

    size = resolved.stat().st_size
    if size > MAX_READ_BYTES:
        return (
            f"Error: file is {size:,} bytes (limit {MAX_READ_BYTES:,}). "
            "Use start_line/end_line to read sections."
        )

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading {path}: {exc}"

    if start_line is not None or end_line is not None:
        lines = content.splitlines(keepends=True)
        sl = max(0, (start_line or 1) - 1)
        el = end_line if end_line is not None else len(lines)
        content = "".join(lines[sl:el])

    logger.debug(f"[fs] read_file: {path} ({len(content)} chars)")
    return content


async def write_file(path: str, content: str, encoding: str = "utf-8") -> str:
    """
    Write text content to a file, creating it (and parent dirs) if needed.

    Args:
        path: Destination path (absolute /workspace/... or relative).
        content: Raw text to write. Do NOT wrap in markdown code fences.
        encoding: File encoding â€” utf-8 (default).
    """
    try:
        resolved = _safe_path(path)
    except ValueError as exc:
        return f"Error: {exc}"

    resolved.parent.mkdir(parents=True, exist_ok=True)

    try:
        resolved.write_text(content, encoding=encoding)
        logger.debug(f"[fs] write_file: {path} ({len(content)} chars)")
        return f"OK: wrote {len(content):,} chars to {path}"
    except Exception as exc:
        return f"Error writing {path}: {exc}"


async def list_dir(
    directory: str = "/workspace",
    recursive: bool = False,
    path: str = None,
) -> str:
    """
    List files and subdirectories at the given path.

    Args:
        directory: Directory to list (absolute or relative to /workspace).
        recursive: Whether to recurse into subdirectories. Default False.
        path: Alias for directory â€” model may send either name.
    """
    if path is not None:
        directory = path

    try:
        resolved = _safe_path(directory)
    except ValueError as exc:
        return f"Error: {exc}"

    if not resolved.exists():
        return f"Error: directory not found: {directory}"
    if not resolved.is_dir():
        return f"Error: path is not a directory: {directory}"

    entries: list[str] = []

    if recursive:
        for item in sorted(resolved.rglob("*"))[:500]:
            rel = item.relative_to(resolved)
            prefix = "  " * (len(rel.parts) - 1)
            kind = "/" if item.is_dir() else ""
            entries.append(f"{prefix}{item.name}{kind}")
    else:
        for item in sorted(resolved.iterdir()):
            kind = "/" if item.is_dir() else f"  ({item.stat().st_size:,} bytes)"
            entries.append(f"{item.name}{kind}")

    if not entries:
        return f"(empty directory: {directory})"

    header = f"{directory}/ â€” {len(entries)} items"
    return header + "\n" + "\n".join(entries)


async def search_files(
    pattern: str,
    directory: str = "/workspace",
    contains: Optional[str] = None,
    path: str = None,
) -> str:
    """
    Search for files matching a glob pattern, optionally filtering by content.

    Args:
        pattern: Glob pattern, e.g. '*.py', '*config*'.
        directory: Directory to search in.
        contains: Optional text substring â€” only return files containing this.
        path: Alias for directory â€” model may send either name.
    """
    if path is not None:
        directory = path

    try:
        resolved = _safe_path(directory)
    except ValueError as exc:
        return f"Error: {exc}"

    if not resolved.exists():
        return f"Error: directory not found: {directory}"

    matches: list[str] = []
    for root, _dirs, files in os.walk(resolved):
        _dirs[:] = [d for d in _dirs if not d.startswith(".")]
        for fname in fnmatch.filter(files, pattern):
            fpath = Path(root) / fname
            if contains:
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    if contains not in text:
                        continue
                except (OSError, PermissionError):
                    continue
            rel = fpath.relative_to(WORKSPACE_ROOT)
            matches.append(str(rel))
            if len(matches) >= MAX_SEARCH_RESULTS:
                break
        if len(matches) >= MAX_SEARCH_RESULTS:
            break

    if not matches:
        hint = f" containing '{contains}'" if contains else ""
        return f"No files matching '{pattern}'{hint} in {directory}"

    header = f"Found {len(matches)} file(s) matching '{pattern}'"
    if contains:
        header += f" containing '{contains}'"
    return header + "\n" + "\n".join(sorted(matches))


async def delete_file(path: str) -> str:
    """
    Permanently delete a file from /workspace.

    Args:
        path: Path of the file to delete (absolute or relative to /workspace).
    """
    try:
        resolved = _safe_path(path)
    except ValueError as exc:
        return f"Error: {exc}"

    if not resolved.exists():
        return f"Error: file not found: {path}"
    if resolved.is_dir():
        return f"Error: '{path}' is a directory. Use run_command with rm -rf."
    if resolved == WORKSPACE_ROOT:
        return "Error: cannot delete the workspace root."

    try:
        resolved.unlink()
        logger.info(f"[fs] delete_file: {path}")
        return f"OK: deleted {path}"
    except Exception as exc:
        return f"Error deleting {path}: {exc}"


def register_filesystem_tools(registry: ToolRegistry) -> None:
    for fn in [read_file, write_file, list_dir, search_files, delete_file]:
        registry.tool(fn)