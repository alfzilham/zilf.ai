"""
Docker Sandbox Manager â€” lifecycle management for sandbox containers.

Responsibilities:
  - Build / pull the sandbox image if missing
  - Spin up a container with strict resource limits and no network
  - Execute commands inside the running container
  - Collect stdout/stderr with a hard timeout
  - Tear down and optionally remove the container afterward

Usage::

    async with DockerSandbox() as sandbox:
        result = await sandbox.run("python script.py")
        print(result.stdout)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ExecResult:
    """Output from a command executed inside the sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    elapsed_ms: float
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Combined stdout + stderr for display."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(f"[stderr] {self.stderr}")
        return "\n".join(parts) or "(no output)"


# ---------------------------------------------------------------------------
# Docker Sandbox
# ---------------------------------------------------------------------------


class DockerSandbox:
    """
    Manages one Docker sandbox container for the duration of a task.

    Context manager usage (recommended)::

        async with DockerSandbox(image="zilf-ai-sandbox:latest") as sb:
            result = await sb.run("python -c 'print(42)'")

    Manual usage::

        sb = DockerSandbox()
        await sb.start()
        result = await sb.run("ls /workspace")
        await sb.stop()
    """

    def __init__(
        self,
        image: str = "zilf-ai-sandbox:latest",
        workspace_path: str = "./workspace",
        cpu_limit: str = "1.0",
        memory_limit: str = "512m",
        pids_limit: int = 64,
        network_mode: str = "none",
        execution_timeout: int = 30,
        auto_remove: bool = True,
    ) -> None:
        self.image = image
        self.workspace_path = workspace_path
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.pids_limit = pids_limit
        self.network_mode = network_mode
        self.execution_timeout = execution_timeout
        self.auto_remove = auto_remove

        self._container: Any = None
        self._client: Any = None
        self._container_id: str | None = None

    # -----------------------------------------------------------------------
    # Context manager
    # -----------------------------------------------------------------------

    async def __aenter__(self) -> "DockerSandbox":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Spin up the sandbox container."""
        client = self._get_client()

        import os
        abs_workspace = os.path.abspath(self.workspace_path)
        os.makedirs(abs_workspace, exist_ok=True)

        container_name = f"sandbox-{uuid.uuid4().hex[:8]}"
        logger.info(f"[sandbox] Starting container {container_name!r} from {self.image!r}")

        try:
            self._container = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.containers.run(
                    self.image,
                    name=container_name,
                    command="sleep infinity",    # keep alive; we exec into it
                    detach=True,
                    remove=self.auto_remove,
                    # Resource limits
                    cpu_period=100_000,
                    cpu_quota=int(float(self.cpu_limit) * 100_000),
                    mem_limit=self.memory_limit,
                    pids_limit=self.pids_limit,
                    # Isolation
                    network_mode=self.network_mode,
                    security_opt=["no-new-privileges:true"],
                    cap_drop=["ALL"],
                    read_only=False,
                    # Filesystem
                    volumes={
                        abs_workspace: {
                            "bind": "/workspace",
                            "mode": "rw",
                        }
                    },
                    tmpfs={"/tmp": "size=64m,mode=1777"},
                    working_dir="/workspace",
                    # Environment
                    environment={
                        "PYTHONUNBUFFERED": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    user="sandbox",
                )
            )
            self._container_id = self._container.id[:12]
            logger.info(f"[sandbox] Container {self._container_id} running.")

        except Exception as exc:
            logger.error(f"[sandbox] Failed to start container: {exc}")
            raise

    async def stop(self) -> None:
        """Stop and optionally remove the sandbox container."""
        if self._container is None:
            return
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._container.stop(timeout=5)
            )
            logger.info(f"[sandbox] Container {self._container_id} stopped.")
        except Exception as exc:
            logger.warning(f"[sandbox] Error stopping container: {exc}")
        finally:
            self._container = None

    # -----------------------------------------------------------------------
    # Command execution
    # -----------------------------------------------------------------------

    async def run(
        self,
        command: str,
        workdir: str = "/workspace",
        timeout: int | None = None,
    ) -> ExecResult:
        """
        Execute a shell command inside the running sandbox container.

        Args:
            command:  Shell command string (run via /bin/bash -c)
            workdir:  Working directory inside the container
            timeout:  Override the default execution timeout (seconds)

        Returns:
            ExecResult with exit_code, stdout, stderr, elapsed_ms
        """
        if self._container is None:
            raise RuntimeError("Sandbox container is not running. Call start() first.")

        timeout = timeout or self.execution_timeout
        t0 = time.perf_counter()
        timed_out = False

        logger.debug(f"[sandbox:{self._container_id}] $ {command[:120]}")

        try:
            exec_result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._container.exec_run(
                        cmd=["/bin/bash", "-c", command],
                        workdir=workdir,
                        user="sandbox",
                        demux=True,
                    )
                ),
                timeout=timeout,
            )
            exit_code = exec_result.exit_code
            stdout_bytes, stderr_bytes = exec_result.output
            stdout = (stdout_bytes or b"").decode("utf-8", errors="replace").strip()
            stderr = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()

        except asyncio.TimeoutError:
            timed_out = True
            exit_code = -1
            stdout = ""
            stderr = f"Command timed out after {timeout}s"
            logger.warning(f"[sandbox:{self._container_id}] Timeout: {command[:80]}")

        elapsed_ms = (time.perf_counter() - t0) * 1000
        result = ExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            elapsed_ms=round(elapsed_ms, 2),
            timed_out=timed_out,
        )

        log_fn = logger.debug if result.success else logger.warning
        log_fn(
            f"[sandbox:{self._container_id}] exit={exit_code} "
            f"({elapsed_ms:.0f}ms) | {(stdout or stderr)[:80]}"
        )
        return result

    async def write_file(self, path: str, content: str) -> None:
        """Write a file into the sandbox workspace via exec."""
        import shlex
        escaped = content.replace("'", "'\\''")
        await self.run(f"mkdir -p $(dirname '{path}') && cat > '{path}' << 'SANDBOX_EOF'\n{escaped}\nSANDBOX_EOF")

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import docker  # type: ignore[import]
                self._client = docker.from_env()
            except ImportError as exc:
                raise ImportError(
                    "docker package not installed. Run: pip install docker"
                ) from exc
            except Exception as exc:
                raise RuntimeError(
                    f"Could not connect to Docker daemon: {exc}\n"
                    "Make sure Docker Desktop is running."
                ) from exc
        return self._client
