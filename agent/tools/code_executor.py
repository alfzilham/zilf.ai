"""
Code Executor Tool â€” runs Python, JavaScript, and Bash snippets in the sandbox.

Different from run_command (which runs arbitrary shell commands):
  - run_code is for short snippet execution and quick verification
  - Automatically wraps the snippet in the correct interpreter call
  - Returns both stdout and any raised exceptions in a structured way
  - Supports Python, JavaScript (node), and Bash
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from loguru import logger

from agent.tools.registry import ToolRegistry
from agent.tools.filesystem import WORKSPACE_ROOT

MAX_OUTPUT_CHARS = 4_000
SUPPORTED_LANGUAGES = ("python", "javascript", "bash")


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------


async def run_code(
    code: str,
    language: Optional[str] = "python",
    timeout_seconds: Optional[int] = 30,
) -> str:
    """
    Execute a code snippet and return its output.

    Writes the snippet to a temporary file in /workspace/.tmp/
    and runs it via the appropriate interpreter inside the sandbox.
    Returns stdout + stderr combined.

    Use this to verify generated code, run quick calculations,
    or test a function before writing it to a permanent file.

    Args:
        code: The source code snippet to execute. Raw code â€” not markdown.
        language: Programming language: 'python' (default), 'javascript', or 'bash'.
        timeout_seconds: Execution timeout in seconds. Default 30, max 120.
    """
    lang = (language or "python").lower().strip()
    if lang not in SUPPORTED_LANGUAGES:
        return f"Error: unsupported language '{lang}'. Choose from: {SUPPORTED_LANGUAGES}"

    timeout = min(int(timeout_seconds or 30), 120)

    # Write snippet to a temp file in workspace
    tmp_dir = WORKSPACE_ROOT / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    ext = {"python": ".py", "javascript": ".js", "bash": ".sh"}[lang]
    tmp_file = tmp_dir / f"snippet_{os.getpid()}{ext}"

    try:
        tmp_file.write_text(code, encoding="utf-8")

        # Build interpreter command
        container_path = f"/workspace/.tmp/{tmp_file.name}"
        if lang == "python":
            cmd = f"python {container_path}"
        elif lang == "javascript":
            cmd = f"node {container_path}"
        else:  # bash
            cmd = f"bash {container_path}"

        logger.debug(f"[code_exec] Running {lang} snippet ({len(code)} chars)")

        # Dispatch via terminal tool
        from agent.tools.terminal import run_command
        output = await run_command(cmd, timeout_seconds=timeout)

        return output[:MAX_OUTPUT_CHARS] if len(output) > MAX_OUTPUT_CHARS else output

    finally:
        # Clean up temp file
        try:
            tmp_file.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_code_executor_tools(registry: ToolRegistry) -> None:
    """Register code executor tools into the given registry."""
    registry.tool(run_code)
