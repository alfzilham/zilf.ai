"""
Worker Agent â€” executes one focused subtask assigned by the Supervisor.

A Worker is a stripped-down Agent that:
  - Receives a subtask via the MessageBus
  - Runs a ReAct loop focused only on that subtask
  - Reports result (or failure) back to the supervisor
  - Has a configurable role/persona (coder, reviewer, tester, etc.)

Roles available:
  coder      â€” writes and modifies code
  reviewer   â€” checks code quality, security, best practices
  tester     â€” creates and runs tests
  documenter â€” writes docstrings and documentation
  devops     â€” handles deployment and infrastructure tasks

Usage::

    worker = WorkerAgent(
        worker_id="coder_1",
        role="coder",
        llm=AnthropicLLM(),
        tool_registry=ToolRegistry.default(),
        bus=message_bus,
    )
    await worker.start()   # listens for TASK_ASSIGNED messages
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from agent.multi_agent.message_bus import AgentMessage, MessageBus, MessageType


# ---------------------------------------------------------------------------
# Role system prompts
# ---------------------------------------------------------------------------

ROLE_PROMPTS: dict[str, str] = {
    "coder": (
        "You are a Senior Software Engineer. Your job is to write clean, "
        "well-documented, production-quality code. "
        "Always read existing files before editing them. "
        "Run tests after every significant change. "
        "Focus only on the assigned subtask â€” do not refactor unrelated code."
    ),
    "reviewer": (
        "You are a Code Quality Specialist. Your job is to review code for "
        "correctness, security vulnerabilities, style violations, and maintainability. "
        "Use search_files to locate the code. Use run_command to run linters. "
        "Produce a structured review: Critical / Major / Minor issues, then recommendations."
    ),
    "tester": (
        "You are a QA Engineer. Your job is to write comprehensive tests. "
        "Read the source file first, then write pytest tests covering: "
        "happy path, edge cases, error conditions. "
        "Run the tests with run_command and report pass/fail counts."
    ),
    "documenter": (
        "You are a Technical Writer and Python developer. Your job is to write "
        "clear, accurate documentation. Add Google-style docstrings to all public "
        "functions and classes. Do not change any logic â€” only add/update comments."
    ),
    "devops": (
        "You are a DevOps Engineer. Your job is to handle deployment, "
        "containerization, and infrastructure tasks. "
        "Write Dockerfiles, docker-compose configs, or CI pipeline steps as needed."
    ),
    "architect": (
        "You are a Software Architect. Your job is to make high-level design decisions. "
        "Analyse the codebase structure and produce a concise architecture recommendation "
        "with rationale. Do not write implementation code unless explicitly asked."
    ),
}


# ---------------------------------------------------------------------------
# Worker Agent
# ---------------------------------------------------------------------------


class WorkerAgent:
    """
    Autonomous worker agent with a specific role.

    Listens on the MessageBus for TASK_ASSIGNED messages, executes the
    subtask using a ReAct loop, then sends TASK_RESULT or TASK_FAILED back.
    """

    def __init__(
        self,
        worker_id: str,
        role: str,
        llm: Any,
        tool_registry: Any,
        bus: MessageBus,
        max_steps: int = 20,
    ) -> None:
        self.worker_id = worker_id
        self.role = role
        self.llm = llm
        self.tool_registry = tool_registry
        self.bus = bus
        self.max_steps = max_steps
        self._subscription: Any = None
        self._running = False
        self._task_count = 0

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to the bus and start listening for tasks."""
        self._subscription = await self.bus.subscribe(self.worker_id)
        self._running = True
        logger.info(f"[worker:{self.worker_id}] Started (role={self.role})")
        asyncio.create_task(self._listen_loop())

    async def stop(self) -> None:
        self._running = False
        if self._subscription:
            await self._subscription.unsubscribe()
        logger.info(f"[worker:{self.worker_id}] Stopped after {self._task_count} tasks")

    # -----------------------------------------------------------------------
    # Message loop
    # -----------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        while self._running:
            if self._subscription is None:
                await asyncio.sleep(0.1)
                continue

            msg = await self._subscription.receive(timeout=5.0)
            if msg is None:
                continue

            if msg.type == MessageType.TASK_ASSIGNED:
                await self._handle_task(msg)

    async def _handle_task(self, msg: AgentMessage) -> None:
        """Execute the assigned subtask and report the result."""
        subtask_id = msg.payload.get("subtask_id", "unknown")
        description = msg.payload.get("description", "")
        supervisor_id = msg.sender

        self._task_count += 1
        logger.info(f"[worker:{self.worker_id}] Received task {subtask_id}: {description[:80]}")

        # Status update: started
        await self.bus.send(
            sender=self.worker_id,
            recipient=supervisor_id,
            msg_type=MessageType.STATUS_UPDATE,
            payload={"subtask_id": subtask_id, "status": "running", "worker": self.worker_id},
        )

        try:
            result = await self._execute(description)

            await self.bus.send(
                sender=self.worker_id,
                recipient=supervisor_id,
                msg_type=MessageType.TASK_RESULT,
                payload={
                    "subtask_id": subtask_id,
                    "worker": self.worker_id,
                    "result": result.get("answer", ""),
                    "success": result.get("success", False),
                    "steps": result.get("steps", 0),
                    "tokens": result.get("tokens", 0),
                },
            )
            logger.info(f"[worker:{self.worker_id}] Completed {subtask_id}")

        except Exception as exc:
            logger.error(f"[worker:{self.worker_id}] Failed {subtask_id}: {exc}")
            await self.bus.send(
                sender=self.worker_id,
                recipient=supervisor_id,
                msg_type=MessageType.TASK_FAILED,
                payload={
                    "subtask_id": subtask_id,
                    "worker": self.worker_id,
                    "error": str(exc),
                },
            )

    async def _execute(self, task: str) -> dict[str, Any]:
        """Run a focused ReAct loop for the given task."""
        from agent.core.agent import Agent

        system = ROLE_PROMPTS.get(self.role, ROLE_PROMPTS["coder"])
        agent = Agent(
            llm=self.llm,
            tool_registry=self.tool_registry,
            max_steps=self.max_steps,
            use_planner=False,
            verbose=False,
        )

        # Inject role context into the task
        full_task = f"[Role: {self.role}]\n\n{task}"
        response = await agent.run(full_task)

        return {
            "answer": response.final_answer or response.error or "",
            "success": response.success,
            "steps": response.steps_taken,
            "tokens": response.total_input_tokens + response.total_output_tokens,
        }

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def tasks_completed(self) -> int:
        return self._task_count

    def __repr__(self) -> str:
        return f"WorkerAgent(id={self.worker_id!r}, role={self.role!r})"
