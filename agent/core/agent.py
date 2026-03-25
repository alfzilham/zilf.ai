"""
Agent — top-level orchestrator.

The Agent class is the public entry point. It:
  1. Receives a task string from the user
  2. Optionally plans the task using TaskPlanner
  3. Runs the ReasoningLoop until completion
  4. Returns a structured AgentResponse

Fixes applied:
  B11 — step_callback as proper __init__ parameter
  B13 — Failed runs saved to memory for learning
  B17 — run_sync() handles nested event loops

Usage::

    from agent.core.agent import Agent
    from agent.llm.anthropic_provider import AnthropicLLM
    from agent.tools.registry import ToolRegistry

    agent = Agent(llm=AnthropicLLM(), tool_registry=ToolRegistry.default())
    result = await agent.run("Write a Python script that fetches GitHub trending repos")
    print(result.final_answer)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from loguru import logger

from agent.core.memory import MemoryManager
from agent.core.reasoning_loop import ReasoningLoop
from agent.core.state import AgentState, AgentStatus, ReasoningStep, TaskPlan
from agent.core.task_planner import TaskPlanner


# ---------------------------------------------------------------------------
# Agent response (returned to the caller)
# ---------------------------------------------------------------------------


class AgentResponse:
    """Structured result returned after a task run completes."""

    def __init__(self, state: AgentState) -> None:
        self.run_id = state.run_id
        self.task = state.task
        self.status = state.status
        self.final_answer = state.final_answer
        self.error = state.error
        self.steps_taken = state.current_step
        self.total_input_tokens = state.total_input_tokens
        self.total_output_tokens = state.total_output_tokens
        self.started_at = state.started_at
        self.completed_at = state.completed_at
        self._state = state  # keep full state for debugging

    @property
    def success(self) -> bool:
        return self.status == AgentStatus.COMPLETE

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def __repr__(self) -> str:
        return (
            f"AgentResponse(status={self.status.value!r}, "
            f"steps={self.steps_taken}, "
            f"tokens={self.total_input_tokens + self.total_output_tokens})"
        )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class Agent:
    """
    Autonomous AI coding agent.

    Orchestrates: TaskPlanner → ReasoningLoop → MemoryManager

    Args:
        llm:            LLM provider instance (AnthropicLLM, OpenAILLM, OllamaLLM)
        tool_registry:  Registry of all available tools
        memory:         Optional MemoryManager; created automatically if omitted
        max_steps:      Hard cap on reasoning iterations (default 30)
        use_planner:    Whether to run task decomposition before the loop (default True)
        verbose:        Stream step-by-step logs to stdout (default True)
        step_callback:  B11 FIX — Optional async callback for real-time step streaming.
                        Signature: async def callback(step: ReasoningStep) -> None
    """

    def __init__(
        self,
        llm: Any,
        tool_registry: Any,
        memory: MemoryManager | None = None,
        max_steps: int = 30,
        use_planner: bool = True,
        verbose: bool = True,
        step_callback: Callable[[ReasoningStep], Awaitable[None]] | None = None,
    ) -> None:
        self.llm = llm
        self.tool_registry = tool_registry
        self.memory = memory or MemoryManager()
        self.max_steps = max_steps
        self.use_planner = use_planner
        self.verbose = verbose

        # B11 FIX: step_callback passed properly through __init__
        # instead of being set via agent._loop.step_callback
        self._loop = ReasoningLoop(
            llm=llm,
            tool_registry=tool_registry,
            max_steps=max_steps,
            verbose=verbose,
            step_callback=step_callback,
        )
        self._planner = TaskPlanner(llm=llm)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def run(self, task: str, run_id: str | None = None, use_planner: bool | None = None) -> AgentResponse:
        """
        Execute a task end-to-end and return an AgentResponse.

        B13 FIX: Both successful AND failed runs are saved to memory,
        so the agent can learn from failures in future runs.
        """
        rid = run_id or str(uuid.uuid4())[:12]
        logger.info(f"[agent:{rid}] ▶ Task: {task!r}")

        # Build initial state
        state = AgentState(
            run_id=rid,
            task=task,
            max_steps=self.max_steps,
        )

        should_plan = self.use_planner if use_planner is None else use_planner
        if should_plan and len(task) < 200:
            task_lower = task.lower()
            if not any(w in task_lower for w in ["plan", "steps", "phase", "first", "then"]):
                logger.info(f"[agent:{rid}] Task is simple, skipping planner.")
                should_plan = False

        # Phase 1: Plan
        if should_plan:
            try:
                state.status = AgentStatus.PLANNING
                state.plan = await self._planner.plan(task, run_id=rid)
                self._log_plan(state.plan)
            except Exception as exc:
                logger.warning(f"[agent:{rid}] Planning failed ({exc}), proceeding without plan.")
                state.plan = None

        # Phase 2: Reason → Act → Observe → Reflect loop
        state = await self._loop.run(state)

        # Phase 3: Persist to long-term memory
        # B13 FIX: Save ALL runs (success + failure) to memory
        self._save_to_memory(state, rid, task)

        self.memory.clear_session()
        response = AgentResponse(state)
        self._log_summary(response)
        return response

    # -----------------------------------------------------------------------
    # Convenience sync wrapper (for CLI / scripts)
    # -----------------------------------------------------------------------

    def run_sync(self, task: str, run_id: str | None = None) -> AgentResponse:
        """
        Synchronous wrapper around `run()` for non-async callers.

        B17 FIX: Handles nested event loops gracefully.
        Uses nest_asyncio if available, falls back to new thread if needed.
        """
        import asyncio

        # Check if there's already a running event loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(self.run(task, run_id=run_id))

        # Running inside an existing event loop (e.g., Jupyter, FastAPI)
        # Try nest_asyncio first
        try:
            import nest_asyncio  # type: ignore[import]
            nest_asyncio.apply()
            return asyncio.run(self.run(task, run_id=run_id))
        except ImportError:
            pass

        # Fallback: run in a separate thread with its own event loop
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, self.run(task, run_id=run_id))
            return future.result(timeout=600)  # 10 minute timeout

    # -----------------------------------------------------------------------
    # Private: Memory persistence (B13 FIX)
    # -----------------------------------------------------------------------

    def _save_to_memory(self, state: AgentState, rid: str, task: str) -> None:
        """
        B13 FIX: Save both successful and failed runs to long-term memory.

        For successful runs: save task + result (as before).
        For failed runs: save task + error + steps taken, so the agent
        can learn from failures and avoid repeating mistakes.
        """
        if state.status == AgentStatus.COMPLETE and state.final_answer:
            # Success — save task + result
            self.memory.memorize(
                content=f"Task: {task}\nResult: {state.final_answer}",
                metadata={
                    "run_id": rid,
                    "steps": state.current_step,
                    "status": state.status.value,
                    "type": "success",
                },
            )
        elif state.status in (AgentStatus.FAILED, AgentStatus.MAX_STEPS_REACHED):
            # B13 FIX: Failed run — save task + error + what was tried
            error_summary = state.error or "Unknown error"

            # Collect what tools were attempted
            tools_tried: list[str] = []
            for step in state.steps:
                for tc in step.tool_calls:
                    tools_tried.append(tc.tool_name)
                if step.reflection:
                    tools_tried.append(f"reflection: {step.reflection[:80]}")

            # Collect last few thoughts for context
            last_thoughts: list[str] = []
            for step in state.steps[-3:]:
                if step.thought:
                    last_thoughts.append(
                        f"Step {step.step_number}: {step.thought[:150]}"
                    )

            failure_content = (
                f"Task: {task}\n"
                f"Status: {state.status.value}\n"
                f"Error: {error_summary}\n"
                f"Steps taken: {state.current_step}\n"
                f"Tools tried: {', '.join(tools_tried[:10])}\n"
                f"Last thoughts:\n" + "\n".join(last_thoughts)
            )

            self.memory.memorize(
                content=failure_content,
                metadata={
                    "run_id": rid,
                    "steps": state.current_step,
                    "status": state.status.value,
                    "type": "failure",
                    "error": error_summary[:200],
                },
            )
            logger.info(
                f"[agent:{rid}] 📝 Failed run saved to memory for future learning."
            )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _log_plan(self, plan: TaskPlan) -> None:
        if not self.verbose:
            return
        logger.info(f"  📋 Plan ({len(plan.subtasks)} subtasks, ~{plan.estimated_steps} steps):")
        for s in plan.subtasks:
            deps = f" [after: {', '.join(s.depends_on)}]" if s.depends_on else ""
            logger.info(f"     • {s.id}: {s.title}{deps}")

    def _log_summary(self, resp: AgentResponse) -> None:
        if not self.verbose:
            return
        icon = "✅" if resp.success else "❌"
        if resp.duration_seconds:
            logger.info(
                f"[agent:{resp.run_id}] {icon} Done — "
                f"status={resp.status.value}, "
                f"steps={resp.steps_taken}, "
                f"tokens={resp.total_input_tokens + resp.total_output_tokens}, "
                f"time={resp.duration_seconds:.1f}s"
            )
        else:
            logger.info(
                f"[agent:{resp.run_id}] {icon} Done — "
                f"status={resp.status.value}, "
                f"steps={resp.steps_taken}, "
                f"tokens={resp.total_input_tokens + resp.total_output_tokens}"
            )