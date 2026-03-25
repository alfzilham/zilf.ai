"""
Reasoning Loop — the heart of the Hams AI.

Implements the Perceive → Reason → Act → Observe → Reflect cycle
based on the ReAct (Reasoning + Acting) framework.

Each call to `run_step()` performs one full iteration:
  1. Perceive  — build context from current AgentState
  2. Reason    — call LLM to get thought + action decision
  3. Act       — execute chosen tools in the sandbox
  4. Observe   — collect tool outputs
  5. Reflect   — update state, check if done

Fixes applied:
  B5  — Smart context windowing (keep task + summarize old, trim tool outputs)
  B12 — Dynamic workspace path from env/config
  B16 — Enhanced _reflect() with output quality check
  B20 — EpisodicMemory integration (lessons learned + episode recording)
  B21 — Parallel tool execution via asyncio.gather
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

from loguru import logger

from agent.core.state import (
    ActionType,
    AgentState,
    AgentStatus,
    ReasoningStep,
    StepStatus,
    ToolCall,
    ToolResult,
)

if TYPE_CHECKING:
    from agent.llm.base import BaseLLM
    from agent.memory.episodic_memory import Episode, EpisodicMemory
    from agent.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Protocol for the LLM response
# ---------------------------------------------------------------------------


class LLMResponse(Protocol):
    """Minimal interface we expect back from any LLM provider."""

    thought: str
    action_type: ActionType
    tool_calls: list[ToolCall]
    final_answer: str | None
    input_tokens: int
    output_tokens: int


# ---------------------------------------------------------------------------
# B5 FIX: Smart Context Windowing Constants
# ---------------------------------------------------------------------------

MAX_CONTEXT_CHARS    = 48_000
MAX_TOOL_OUTPUT_CHARS = 3_000
MIN_RECENT_STEPS     = 4
KEEP_FIRST_MESSAGES  = 2
KEEP_LAST_MESSAGES   = 10

# How many past episodes to surface as "lessons learned"
MAX_LESSONS          = 3


# ---------------------------------------------------------------------------
# B12 FIX: Dynamic workspace path
# ---------------------------------------------------------------------------


def _get_workspace_path() -> str:
    env_path = os.environ.get("AGENT_WORKSPACE")
    if env_path:
        return env_path
    if os.path.exists("/.dockerenv") or os.path.isdir("/workspace"):
        return "/workspace"
    return os.path.join(os.getcwd(), "workspace")


# ---------------------------------------------------------------------------
# Reasoning Loop
# ---------------------------------------------------------------------------


class ReasoningLoop:
    """
    Drives the agent through its Perceive → Reason → Act → Observe → Reflect cycle.

    Args:
        llm:            LLM provider instance.
        tool_registry:  Registry of all available tools.
        max_steps:      Hard cap on iterations.
        verbose:        Log step-by-step to stdout.
        step_callback:  Optional async callback called after each step.
        episodic_memory: Optional EpisodicMemory for lessons-learned injection
                         and automatic episode recording.

    Usage::

        loop = ReasoningLoop(
            llm=claude,
            tool_registry=registry,
            episodic_memory=EpisodicMemory(),
        )
        state = await loop.run(state)
    """

    def __init__(
        self,
        llm: "BaseLLM",
        tool_registry: "ToolRegistry",
        max_steps: int = 30,
        verbose: bool = True,
        step_callback: Callable[[ReasoningStep], Awaitable[None]] | None = None,
        episodic_memory: "EpisodicMemory | None" = None,
    ) -> None:
        self.llm             = llm
        self.tool_registry   = tool_registry
        self.max_steps       = max_steps
        self.verbose         = verbose
        self.step_callback   = step_callback
        self.episodic_memory = episodic_memory

        # Populated at start of run() — injected into every system prompt
        self._lessons_prompt: str = ""

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def run(self, state: AgentState) -> AgentState:
        """
        Run the full reasoning loop until complete, final_answer, or max_steps.

        B20: If episodic_memory is provided:
          - Searches for similar past episodes at the start and injects
            lessons into the system prompt.
          - Records this run as a new episode at the end.
        """
        logger.info(f"[run:{state.run_id}] Starting reasoning loop — task: {state.task!r}")
        state.status = AgentStatus.RUNNING
        run_start    = time.monotonic()

        # ── B20: Load lessons from past episodes ──────────────────────────
        self._lessons_prompt = ""
        if self.episodic_memory is not None:
            self._lessons_prompt = self._build_lessons_prompt(state.task)
            if self._lessons_prompt and self.verbose:
                logger.info(
                    f"[run:{state.run_id}] Injecting lessons from "
                    f"{len(self.episodic_memory.search(state.task, n=MAX_LESSONS))} past episodes"
                )

        # ── Main loop ─────────────────────────────────────────────────────
        while not state.is_done:
            if state.current_step >= self.max_steps:
                logger.warning(f"[run:{state.run_id}] Max steps ({self.max_steps}) reached.")
                state.status = AgentStatus.MAX_STEPS_REACHED
                state.error  = f"Stopped after {self.max_steps} steps without completing the task."
                break

            state = await self.run_step(state)

        if state.status == AgentStatus.RUNNING:
            state.status = AgentStatus.FAILED
            state.error  = "Loop exited in RUNNING state — unexpected."

        logger.info(f"[run:{state.run_id}] Loop finished — status={state.status}")

        # ── B20: Record this run as an episode ────────────────────────────
        if self.episodic_memory is not None:
            elapsed = time.monotonic() - run_start
            self._record_episode(state, elapsed)

        return state

    async def run_step(self, state: AgentState) -> AgentState:
        """Execute one full Perceive → Reason → Act → Observe → Reflect iteration."""
        step_num = state.current_step + 1
        step     = ReasoningStep(step_number=step_num, status=StepStatus.RUNNING)

        if self.verbose:
            logger.info(f"  ── Step {step_num}/{self.max_steps} ──")

        try:
            # 1. PERCEIVE
            messages = self._perceive(state)

            # 2. REASON
            llm_response = await self.llm.generate(
                messages=messages,
                tools=self.tool_registry.tool_schemas(),
                system=self._system_prompt(state),
            )
            step.thought      = llm_response.thought
            step.action_type  = llm_response.action_type
            step.tool_calls   = llm_response.tool_calls
            step.final_answer = llm_response.final_answer
            step.input_tokens  = llm_response.input_tokens
            step.output_tokens = llm_response.output_tokens

            if self.verbose and step.thought:
                logger.debug(f"  💭 Thought: {step.thought[:200]}")

            # 3. ACT + 4. OBSERVE
            if step.action_type == ActionType.FINAL_ANSWER:
                step.tool_results = []
                step.mark_complete(StepStatus.SUCCESS)
                state.add_step(step)
                self._finish(state, step)
                await self._fire_callback(step)
                return state

            # B21: parallel tool execution
            step.tool_results = await self._act_and_observe(step.tool_calls)

            # 5. REFLECT
            self._reflect(state, step)
            step.mark_complete(StepStatus.SUCCESS)

        except Exception as exc:
            logger.exception(f"  Step {step_num} raised an exception: {exc}")
            step.status     = StepStatus.FAILED
            step.reflection = f"Step failed with error: {exc}"
            state.add_step(step)
            state.status = AgentStatus.FAILED
            state.error  = str(exc)
            await self._fire_callback(step)
            return state

        state.add_step(step)
        await self._fire_callback(step)
        return state

    # -----------------------------------------------------------------------
    # Private: Callback
    # -----------------------------------------------------------------------

    async def _fire_callback(self, step: ReasoningStep) -> None:
        if self.step_callback is None:
            return
        try:
            await self.step_callback(step)
        except Exception as exc:
            logger.warning(f"[loop] step_callback raised: {exc} — ignoring")

    # -----------------------------------------------------------------------
    # Private: Perceive (B5 FIX — Smart Context Windowing)
    # -----------------------------------------------------------------------

    def _perceive(self, state: AgentState) -> list[dict]:
        messages: list[dict] = [{"role": "user", "content": state.task}]
        all_context    = state.context_messages()
        trimmed_context = self._trim_tool_outputs(all_context)
        total = sum(len(str(m.get("content", ""))) for m in trimmed_context)

        if total <= MAX_CONTEXT_CHARS:
            messages.extend(trimmed_context)
        else:
            messages.extend(self._smart_trim(trimmed_context, state))

        return messages

    def _trim_tool_outputs(self, messages: list[dict]) -> list[dict]:
        result = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                new_content = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_content = block.get("content", "")
                        if isinstance(tool_content, str) and len(tool_content) > MAX_TOOL_OUTPUT_CHARS:
                            half = MAX_TOOL_OUTPUT_CHARS // 2
                            truncated = (
                                tool_content[:half]
                                + f"\n\n... [TRUNCATED {len(tool_content) - MAX_TOOL_OUTPUT_CHARS} chars] ...\n\n"
                                + tool_content[-half:]
                            )
                            new_content.append({**block, "content": truncated})
                        else:
                            new_content.append(block)
                    else:
                        new_content.append(block)
                result.append({**msg, "content": new_content})
            elif isinstance(content, str) and len(content) > MAX_TOOL_OUTPUT_CHARS:
                half = MAX_TOOL_OUTPUT_CHARS // 2
                truncated = (
                    content[:half]
                    + f"\n\n... [TRUNCATED {len(content) - MAX_TOOL_OUTPUT_CHARS} chars] ...\n\n"
                    + content[-half:]
                )
                result.append({**msg, "content": truncated})
            else:
                result.append(msg)
        return result

    def _smart_trim(self, messages: list[dict], state: AgentState) -> list[dict]:
        total_msgs = len(messages)
        if total_msgs <= KEEP_FIRST_MESSAGES + KEEP_LAST_MESSAGES:
            return messages

        first_part   = messages[:KEEP_FIRST_MESSAGES]
        last_part    = messages[-KEEP_LAST_MESSAGES:]
        dropped_count = total_msgs - KEEP_FIRST_MESSAGES - KEEP_LAST_MESSAGES

        trim_parts = [
            f"[CONTEXT TRIMMED: {dropped_count} messages removed to fit context window]",
        ]
        if state.plan:
            completed = len(state.plan.completed_subtasks)
            total     = len(state.plan.subtasks)
            trim_parts.append(f"Plan progress: {completed}/{total} subtasks completed.")

        # Use step-based index (not message-based) to avoid off-by-one
        mid_steps = state.steps[1:-2] if len(state.steps) > 3 else []
        if mid_steps:
            key_facts = []
            for step in mid_steps[:5]:
                if step.thought:
                    key_facts.append(f"  Step {step.step_number}: {step.thought[:100]}")
                for tr in step.tool_results:
                    icon = "✅" if tr.success else "❌"
                    key_facts.append(
                        f"    {icon} {tr.tool_name}: {(tr.output or tr.error or '')[:80]}"
                    )
            if key_facts:
                trim_parts.append("Key actions from trimmed context:")
                trim_parts.extend(key_facts)

        trim_marker = {"role": "user", "content": "\n".join(trim_parts)}
        logger.debug(
            f"[loop] Smart trim: {total_msgs} → keep {KEEP_FIRST_MESSAGES}+{KEEP_LAST_MESSAGES}, "
            f"dropped {dropped_count}"
        )
        return first_part + [trim_marker] + last_part

    # -----------------------------------------------------------------------
    # Private: Act + Observe  (B21 — Parallel execution)
    # -----------------------------------------------------------------------

    async def _act_and_observe(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """
        B21: Execute all tool calls IN PARALLEL via asyncio.gather.

        Previously sequential — if the agent called 3 tools, they ran one at a time.
        Now they fire concurrently, which matters for file reads, web searches, etc.

        Results are returned in the same order as tool_calls (gather preserves order).
        """
        if not tool_calls:
            return []

        if self.verbose:
            names = [tc.tool_name for tc in tool_calls]
            logger.info(f"  🔧 Dispatching {len(tool_calls)} tool(s) in parallel: {names}")

        async def _dispatch_one(tc: ToolCall) -> ToolResult:
            t0 = time.perf_counter()
            try:
                output  = await self.tool_registry.dispatch(tc.tool_name, tc.tool_input)
                elapsed = (time.perf_counter() - t0) * 1000
                result  = ToolResult(
                    tool_name=tc.tool_name,
                    tool_use_id=tc.tool_use_id,
                    output=str(output),
                    elapsed_ms=round(elapsed, 2),
                )
                if self.verbose:
                    logger.debug(f"  ✅ {tc.tool_name}: {str(output)[:120]}")
            except Exception as exc:
                elapsed = (time.perf_counter() - t0) * 1000
                result  = ToolResult(
                    tool_name=tc.tool_name,
                    tool_use_id=tc.tool_use_id,
                    output="",
                    error=str(exc),
                    elapsed_ms=round(elapsed, 2),
                )
                logger.warning(f"  ❌ {tc.tool_name} failed: {exc}")
            return result

        return list(await asyncio.gather(*[_dispatch_one(tc) for tc in tool_calls]))

    # -----------------------------------------------------------------------
    # Private: Reflect (B16 FIX — Enhanced with quality check)
    # -----------------------------------------------------------------------

    def _reflect(self, state: AgentState, step: ReasoningStep) -> None:
        reflections: list[str] = []

        failed = [r for r in step.tool_results if not r.success]
        if failed:
            for r in failed:
                error_text = (r.error or r.output or "").lower()
                recoverable_keywords = [
                    "not installed", "modulenotfounderror", "importerror",
                    "no module named", "run: pip install", "pip install",
                ]
                if any(kw in error_text for kw in recoverable_keywords):
                    import re
                    pkg_match = re.search(r'pip install ([\w\-]+)', r.error or r.output or "", re.IGNORECASE)
                    pkg = pkg_match.group(1) if pkg_match else "the missing package"
                    reflections.append(
                        f"⚠️ Tool `{r.tool_name}` failed because a dependency is missing. "
                        f"REQUIRED NEXT ACTION: Call run_command with "
                        f'command="pip install {pkg}" to install it, '
                        f"then retry {r.tool_name} with the same arguments. "
                        f"Do NOT give a final_answer yet — the task is not complete."
                    )
                else:
                    reflections.append(
                        f"⚠️ Tool `{r.tool_name}` failed: {(r.error or '')[:120]}. "
                        "Will try to recover on the next step."
                    )

        empty_outputs = [
            r for r in step.tool_results
            if r.success and (not r.output or r.output.strip() in ("", "None", "null"))
        ]
        if empty_outputs:
            reflections.append(
                f"⚠️ {len(empty_outputs)} tool(s) returned empty output: "
                f"{[r.tool_name for r in empty_outputs]}. "
                "May need different approach or parameters."
            )

        if len(state.steps) >= 3:
            from collections import Counter
            recent_tools = [
                (tc.tool_name, str(tc.tool_input)[:100])
                for s in state.steps[-3:]
                for tc in s.tool_calls
            ]
            repeated = [(t, c) for t, c in Counter(recent_tools).items() if c >= 3]
            if repeated:
                reflections.append(
                    f"🔄 Possible loop: {[t[0] for t, _ in repeated]} called 3+ times. "
                    "Consider a different approach."
                )

        if state.plan and not state.plan.is_complete:
            completed = len(state.plan.completed_subtasks)
            total     = len(state.plan.subtasks)
            if state.current_step >= 5 and completed == 0:
                reflections.append(
                    f"⚠️ {state.current_step} steps used but 0/{total} subtasks complete. "
                    "Consider simplifying approach."
                )

        step.reflection = " | ".join(reflections) if reflections else "All tools executed successfully."

        if self.verbose and reflections:
            for r in reflections:
                logger.warning(f"  {r}")

    def _finish(self, state: AgentState, step: ReasoningStep) -> None:
        state.final_answer = step.final_answer
        state.status       = AgentStatus.COMPLETE
        state.completed_at = step.completed_at
        if self.verbose:
            logger.success(f"  ✔ Task complete. Answer: {str(step.final_answer)[:200]}")

    # -----------------------------------------------------------------------
    # Private: System prompt (B12 + B20 — workspace path + lessons section)
    # -----------------------------------------------------------------------

    def _system_prompt(self, state: AgentState) -> str:
        """
        B12: Workspace path resolved dynamically.
        B20: Lessons-learned section injected from EpisodicMemory when available.
        """
        workspace  = _get_workspace_path()
        tools_list = "\n".join(
            f"- {name}: {desc}"
            for name, desc in self.tool_registry.tool_descriptions().items()
        )
        plan_section = ""
        if state.plan:
            completed = len(state.plan.completed_subtasks)
            total     = len(state.plan.subtasks)
            plan_section = (
                f"\n\n## Current Plan ({completed}/{total} steps done)\n"
                + "\n".join(
                    f"[{'✓' if s.status.value == 'success' else ' '}] {s.id}: {s.title}"
                    for s in state.plan.subtasks
                )
            )

        # B20: lessons section — only present when memory has relevant episodes
        lessons_section = (
            f"\n\n## Lessons from Past Similar Tasks\n{self._lessons_prompt}"
            if self._lessons_prompt
            else ""
        )

        return f"""You are an expert AI coding agent. Complete the given task autonomously using tools.

## WORKSPACE
- Working directory: {workspace} (persistent storage, gunakan ini)
- Gunakan write_file tool untuk membuat file
- Untuk final answer: tampilkan semua kode lengkap, siap digunakan

## CRITICAL RULES
- NEVER give a final answer before using at least one tool
- NEVER stop after just thinking — you MUST act
- Use tools repeatedly until the task is 100% complete
- Only use final_answer when ALL work is done and verified

# Tool calling format is defined in the provider layer (hams_max_provider.py)

## Available Tools
{tools_list}

## Steps remaining: {state.steps_remaining}{plan_section}{lessons_section}"""

    # -----------------------------------------------------------------------
    # Private: B20 — EpisodicMemory helpers
    # -----------------------------------------------------------------------

    def _build_lessons_prompt(self, task: str) -> str:
        """
        Search episodic memory for similar past tasks and format them
        as a concise "lessons learned" block for the system prompt.

        Only surfaces episodes with reward >= 0.5 (partial or full success).
        Failures are shown separately as cautionary notes.
        """
        if self.episodic_memory is None:
            return ""

        similar = self.episodic_memory.search(task, n=MAX_LESSONS)
        if not similar:
            return ""

        successes = [ep for ep in similar if ep.reward >= 0.5]
        failures  = [ep for ep in similar if ep.reward < 0.5]

        lines: list[str] = []

        if successes:
            lines.append("✅ What worked in similar past tasks:")
            for ep in successes:
                lines.append(f"  • Task: {ep.task[:80]}")
                lines.append(f"    Outcome: {ep.outcome[:120]}")
                if ep.actions:
                    # Show the sequence of tools used — useful pattern hint
                    tool_sequence = " → ".join(
                        a.get("tools", ["?"])[0] if isinstance(a.get("tools"), list) else str(a.get("tools", "?"))
                        for a in ep.actions[:6]
                    )
                    lines.append(f"    Tool sequence: {tool_sequence}")
                lines.append(f"    Reward: {ep.reward:.2f} | Steps: {ep.steps_taken}")

        if failures:
            lines.append("\n⚠️ What did NOT work (avoid these approaches):")
            for ep in failures:
                lines.append(f"  • Task: {ep.task[:80]}")
                lines.append(f"    Failed because: {ep.outcome[:120]}")

        return "\n".join(lines)

    def _calculate_reward(self, state: AgentState) -> float:
        """
        Calculate a partial reward (0.0–1.0) based on run outcome.

        Better than binary 0/1 — gives signal about partial progress.

        Scoring:
          - COMPLETE with final_answer       → 1.0
          - COMPLETE without final_answer    → 0.8
          - MAX_STEPS_REACHED with plan progress → 0.3–0.5
          - FAILED                           → 0.0–0.1
        """
        if state.status == AgentStatus.COMPLETE:
            return 1.0 if state.final_answer else 0.8

        if state.status == AgentStatus.MAX_STEPS_REACHED:
            if state.plan:
                completed = len(state.plan.completed_subtasks)
                total     = len(state.plan.subtasks)
                if total > 0:
                    # Partial credit proportional to plan completion
                    return round(0.3 + 0.2 * (completed / total), 2)
            return 0.2

        if state.status == AgentStatus.FAILED:
            # Tiny reward if at least some steps succeeded
            successful_steps = sum(
                1 for s in state.steps if s.status == StepStatus.SUCCESS
            )
            return round(min(0.1, successful_steps * 0.02), 2)

        return 0.0

    def _record_episode(self, state: AgentState, elapsed_seconds: float) -> None:
        """
        B20: Save the completed run to EpisodicMemory.

        Captures tool sequence, reward, and token usage so future runs
        can learn from this episode.
        """
        if self.episodic_memory is None:
            return

        reward = self._calculate_reward(state)

        # Build action list — one entry per step, recording tool names + brief input
        actions: list[dict] = []
        for step in state.steps:
            if step.tool_calls:
                actions.append({
                    "step":   step.step_number,
                    "tools":  [tc.tool_name for tc in step.tool_calls],
                    "inputs": [
                        {tc.tool_name: str(tc.tool_input)[:80]}
                        for tc in step.tool_calls
                    ],
                    "success": all(tr.success for tr in step.tool_results),
                })

        outcome = (
            state.final_answer
            or state.error
            or f"Status: {state.status.value}"
        )

        # Derive tags from status and plan
        tags: list[str] = [state.status.value]
        if state.plan:
            tags.append("has_plan")
        if reward >= 0.8:
            tags.append("high_reward")

        ep = self.episodic_memory.add_episode(
            task=state.task,
            actions=actions,
            outcome=str(outcome)[:500],
            reward=reward,
            input_tokens=sum(s.input_tokens for s in state.steps),
            output_tokens=sum(s.output_tokens for s in state.steps),
            steps_taken=state.current_step,
            elapsed_seconds=round(elapsed_seconds, 2),
            tags=tags,
        )

        if self.verbose:
            logger.info(
                f"[episodic] Recorded episode {ep.episode_id[:8]} — "
                f"reward={reward:.2f} steps={state.current_step} "
                f"elapsed={elapsed_seconds:.1f}s"
            )