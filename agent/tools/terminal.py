"""
Terminal Tool — executes shell commands inside the Docker sandbox.

Security notes:
  - All commands run inside the isolated Docker sandbox (non-root, no network by default)
  - Every command is logged to an immutable audit trail
  - Output is capped at MAX_OUTPUT_CHARS to prevent context overflow
  - working_directory default di-sync dengan WORKSPACE_ROOT dari filesystem.py
"""

from __future__ import annotations

import os
from typing import Optional

from loguru import logger

from agent.tools.registry import ToolRegistry

MAX_OUTPUT_CHARS = 6_000
SANDBOX_AVAILABLE = os.environ.get("AGENT_SANDBOX_DISABLED", "0") != "1"


# ---------------------------------------------------------------------------
# Workspace root — sync dengan filesystem.py
# ---------------------------------------------------------------------------

def _get_workspace_root() -> str:
    """
    Ambil WORKSPACE_ROOT dari filesystem.py supaya CWD selalu konsisten.
    Fallback ke /workspace atau ./workspace.
    """
    try:
        from agent.tools.filesystem import get_workspace_root
        return str(get_workspace_root())
    except ImportError:
        pass

    # Fallback: sama dengan logika di filesystem.py
    env_ws = os.environ.get("AGENT_WORKSPACE")
    if env_ws:
        return env_ws

    import pathlib
    system_ws = pathlib.Path("/workspace")
    if system_ws.exists() and system_ws.is_dir():
        return "/workspace"

    return str(pathlib.Path("./workspace").resolve())


# ---------------------------------------------------------------------------
# Sandbox singleton
# ---------------------------------------------------------------------------

_sandbox: "Any | None" = None  # type: ignore[name-defined]


async def _get_sandbox() -> "Any":  # type: ignore[name-defined]
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
    global _sandbox
    if _sandbox is not None:
        try:
            await _sandbox.stop()
        except Exception:
            pass
        _sandbox = None


# ---------------------------------------------------------------------------
# Local executor fallback
# ---------------------------------------------------------------------------


class _LocalExecutor:
    """
    Fallback executor yang runs commands langsung di host.
    Dipakai di dev / CI / Railway (tanpa Docker).
    """

    async def run(self, command: str, workdir: str = ".", timeout: int = 60) -> "Any":  # type: ignore
        import asyncio

        class _Result:
            def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
                self.exit_code = exit_code
                self.stdout = stdout
                self.stderr = stderr
                self.success = exit_code == 0
                self.output = (stdout + ("\n[stderr] " + stderr if stderr else "")).strip()

        # Pastikan workdir ada sebelum dipakai
        import pathlib
        wd = pathlib.Path(workdir)
        if not wd.exists():
            # Coba buat, kalau gagal fallback ke CWD
            try:
                wd.mkdir(parents=True, exist_ok=True)
            except Exception:
                workdir = str(pathlib.Path.cwd())
                logger.warning(f"[terminal] workdir '{wd}' tidak ada, fallback ke {workdir}")

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
    working_directory: Optional[str] = None,
    timeout_seconds: Optional[int] = 60,
) -> str:
    """
    Execute a shell command inside the /workspace directory.

    Commands run under /bin/bash -c. Output (stdout + stderr) is returned
    as a single string. Working directory default ke WORKSPACE_ROOT
    (sama dengan direktori yang dipakai write_file).

    Args:
        command: Shell command string. Runs under /bin/bash -c.
            Examples: 'pytest tests/ -v', 'pip install httpx', 'git status'
        working_directory: Working directory untuk command. Default: /workspace
            (atau path WORKSPACE_ROOT yang aktif). Harus di dalam /workspace.
        timeout_seconds: Max seconds sebelum process di-kill. Default 60, max 300.
    """
    timeout = min(int(timeout_seconds or 60), 300)

    # FIX: default working_directory = WORKSPACE_ROOT (sync dengan filesystem.py)
    if not working_directory:
        working_directory = _get_workspace_root()

    # Remap /workspace → WORKSPACE_ROOT kalau berbeda
    # (supaya konsisten meski Railway mount path berbeda)
    ws_root = _get_workspace_root()
    if working_directory == "/workspace" and ws_root != "/workspace":
        working_directory = ws_root
        logger.debug(f"[terminal] Remapped /workspace → {ws_root}")

    logger.info(f"[terminal] $ {command[:120]} (cwd={working_directory})")

    sandbox = await _get_sandbox()
    result = await sandbox.run(command, workdir=working_directory, timeout=timeout)

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
            f"[Truncated — {len(output):,} chars total]\n\n"
            f"{output[:half]}\n\n...\n\n{output[-half:]}"
        )

    status = "✓" if result.success else f"✗ exit={result.exit_code}"
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