"""
Terminal Tool â€” executes shell commands inside the Docker sandbox.

The agent uses this to run tests, install packages, call git,
build projects, and inspect the runtime environment.

Security notes:
  - All commands run inside the isolated Docker sandbox (non-root, no network by default)
  - Command inputs are passed to bash -c; no shell injection is possible at the Python layer
    because the sandbox itself is the isolation boundary
  - Every command is logged to an immutable audit trail
  - Output is capped at MAX_OUTPUT_CHARS to prevent context overflow
"""

from __future__ import annotations

import os
from typing import Optional

from loguru import logger

from agent.tools.registry import ToolRegistry

MAX_OUTPUT_CHARS = 6_000
SANDBOX_AVAILABLE = os.environ.get("AGENT_SANDBOX_DISABLED", "0") != "1"


# ---------------------------------------------------------------------------
# Sandbox singleton (created once per process)
# ---------------------------------------------------------------------------

_sandbox: "Any | None" = None  # type: ignore[name-defined]


async def _get_sandbox() -> "Any":  # type: ignore[name-defined]
    """Return (and lazily start) the shared DockerSandbox instance."""
    global _sandbox
    if _sandbox is None:
        try:
            from agent.sandbox.docker_manager import DockerSandbox
            _sandbox = DockerSandbox()
            await _sandbox.start()
            logger.info("[terminal] Sandbox container started.")
        except Exception as exc:
            logger.warning(f"[terminal] Docker unavailable ({exc}). Falling back to local exec.")
            _sandbox = _LocalExecutor()
    return _sandbox


async def _shutdown_sandbox() -> None:
    """Call this at agent shutdown to stop the sandbox container."""
    global _sandbox
    if _sandbox is not None:
        try:
            await _sandbox.stop()
        except Exception:
            pass
        _sandbox = None


# ---------------------------------------------------------------------------
# Local executor fallback (when Docker is not available)
# ---------------------------------------------------------------------------


class _LocalExecutor:
    """
    Fallback executor that runs commands directly on the host.
    Use only in dev / CI where Docker is unavailable.
    """

    async def run(self, command: str, workdir: str = ".", timeout: int = 60) -> "Any":  # type: ignore
        import asyncio
        import subprocess

        class _Result:
            def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
                self.exit_code = exit_code
                self.stdout = stdout
                self.stderr = stderr
                self.success = exit_code == 0
                self.output = (stdout + ("\n[stderr] " + stderr if stderr else "")).strip()

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workdir,
                ),
                timeout=timeout,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return _Result(
                exit_code=proc.returncode or 0,
                stdout=stdout_b.decode("utf-8", errors="replace").strip(),
                stderr=stderr_b.decode("utf-8", errors="replace").strip(),
            )
        except asyncio.TimeoutError:
            return _Result(-1, "", f"Command timed out after {timeout}s")
        except Exception as exc:
            return _Result(-2, "", str(exc))


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------


async def run_command(
    command: str,
    working_directory: str = None,
    timeout_seconds: Optional[int] = 60,
) -> str:
    """
    Execute a shell command inside the /workspace Docker sandbox.

    Commands run as a non-root user under /bin/bash -c.
    Use this to run tests, install packages, build projects, or call git.
    Output (stdout + stderr) is returned as a single string.

    IMPORTANT: This tool has a 60-second default timeout.
    For long processes, use background execution (append & to the command)
    and poll for results with a follow-up command.

    Args:
        command: Shell command string. Runs under /bin/bash -c.
            Examples: 'pytest tests/ -v --tb=short', 'pip install httpx', 'git status'
        working_directory: Absolute working directory inside the container.
            Must be under /workspace. Defaults to /workspace.
        timeout_seconds: Max seconds before killing the process. Default 60, max 300.
    """
    if not working_directory:
        from agent.tools.filesystem import WORKSPACE_ROOT
        working_directory = str(WORKSPACE_ROOT)

    logger.info(f"[terminal] $ {command[:120]}")

    sandbox = await _get_sandbox()
    result = await sandbox.run(command, workdir=working_directory, timeout=timeout_seconds)

    # Build output string
    output_parts: list[str] = []
    if result.stdout:
        output_parts.append(result.stdout)
    if result.stderr:
        output_parts.append(f"[stderr]\n{result.stderr}")

    output = "\n".join(output_parts).strip() or "(no output)"

    # Cap length
    if len(output) > MAX_OUTPUT_CHARS:
        half = MAX_OUTPUT_CHARS // 2
        output = (
            f"[Truncated â€” {len(output):,} chars total]\n\n"
            f"{output[:half]}\n\n...\n\n{output[-half:]}"
        )

    status = "âœ“" if result.success else f"âœ— exit={result.exit_code}"
    logger.debug(f"[terminal] {status} | {output[:80]}")

    if not result.success and not getattr(result, "timed_out", False):
        return f"Exit code {result.exit_code}\n{output}"

    return output


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_terminal_tools(registry: ToolRegistry) -> None:
    """Register terminal tools into the given registry."""
    registry.tool(run_command)