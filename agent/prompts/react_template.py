"""
ReAct Prompt Templates â€” builds the Thought/Action/Observation message
sequence fed to the LLM on each reasoning step.

The ReAct framework (Reasoning + Acting) interleaves:
  Thought   â€” the agent's reasoning about what to do next
  Action    â€” the tool call it decides to make
  Observation â€” the tool's output

This module provides:
  ReActPromptBuilder   : assembles the full message list for a new LLM call
  format_step_history  : formats past steps as readable context text

Usage::

    builder = ReActPromptBuilder(tool_registry)
    messages = builder.build(state)
    response = await llm.generate(messages=messages, tools=tool_schemas)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Path to the production system prompt
_SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.txt"

# Fallback inline system prompt (used if the file is missing)
_FALLBACK_SYSTEM = (
    "You are a senior software engineer. Complete coding tasks using the "
    "available tools. Think step by step. Always verify your work by running tests."
)


def load_system_prompt() -> str:
    """Load the system prompt from file, falling back to the inline version."""
    try:
        return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _FALLBACK_SYSTEM


# ---------------------------------------------------------------------------
# Step history formatter
# ---------------------------------------------------------------------------


def format_step_history(steps: list[Any]) -> str:
    """
    Convert a list of ReasoningStep objects into a compact Thought/Action/Observation
    text block for inclusion in the context window.

    Keeps the most recent steps readable; older steps are summarised.
    """
    lines: list[str] = []

    for step in steps:
        if step.thought:
            lines.append(f"Thought: {step.thought.strip()}")

        for tc in getattr(step, "tool_calls", []):
            import json
            args_str = json.dumps(tc.tool_input, ensure_ascii=False)
            if len(args_str) > 200:
                args_str = args_str[:197] + "..."
            lines.append(f"Action: {tc.tool_name}({args_str})")

        for tr in getattr(step, "tool_results", []):
            obs = tr.error if tr.error else tr.output
            if len(obs) > 300:
                obs = obs[:297] + "..."
            lines.append(f"Observation: {obs}")

        if step.reflection:
            lines.append(f"Reflection: {step.reflection}")

        lines.append("")  # blank line between steps

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# ReAct prompt builder
# ---------------------------------------------------------------------------

REACT_PREAMBLE = """Use the following format:

Thought: think about what to do next
Action: <tool_name>(<arguments>)
Observation: <tool result>
... (repeat as needed)
Thought: I now have enough information to complete the task
Final Answer: <your complete answer or TASK COMPLETE block>

Begin!
"""


class ReActPromptBuilder:
    """
    Builds the full message list for a reasoning-loop LLM call.

    The message structure:
      [0] system prompt (injected by the LLM provider layer)
      [1] user message: task + ReAct preamble
      [2..N] interleaved assistant/user turns from step history
      [N+1] final "Thought:" prompt to continue the loop
    """

    def __init__(self, include_preamble: bool = True) -> None:
        self.include_preamble = include_preamble
        self._system_prompt = load_system_prompt()

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def build(self, state: Any) -> list[dict[str, Any]]:
        """
        Build the messages list for the next LLM call.

        Args:
            state: AgentState â€” carries task, steps, plan, token counts.

        Returns:
            List of message dicts in OpenAI/Anthropic format.
        """
        messages: list[dict[str, Any]] = []

        # --- First user message: task + format instructions ---
        task_content = state.task
        if self.include_preamble and not state.steps:
            # Only inject the preamble on the very first turn
            task_content = f"{state.task}\n\n{REACT_PREAMBLE}"

        if state.plan:
            done = sum(1 for s in state.plan.subtasks if s.status.value == "success")
            total = len(state.plan.subtasks)
            plan_lines = "\n".join(
                f"  {'âœ“' if s.status.value == 'success' else 'â—‹'} {s.id}: {s.title}"
                for s in state.plan.subtasks
            )
            task_content += (
                f"\n\n## Plan ({done}/{total} complete)\n{plan_lines}"
            )

        messages.append({"role": "user", "content": task_content})

        # --- Interleaved history from AgentState ---
        messages.extend(state.context_messages())

        return messages

    def build_simple(self, task: str, history: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """
        Build a minimal message list for non-stateful use (planner, corrector, etc.).
        """
        msgs: list[dict[str, Any]] = [{"role": "user", "content": task}]
        if history:
            msgs.extend(history)
        return msgs
