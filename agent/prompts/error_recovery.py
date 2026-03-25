"""
Error Recovery Prompts â€” targeted prompts that help the agent recover from
specific failure modes by asking the LLM to self-correct.

Each function returns a (system, user_message) tuple that can be fed directly
to llm.generate_text() to produce a corrected output.

Usage::

    system, user = syntax_correction_prompt(task, bad_code, error)
    fixed_code = await llm.generate_text(
        messages=[{"role": "user", "content": user}],
        system=system,
    )
"""

from __future__ import annotations

from agent.core.exceptions import AgentError


# ---------------------------------------------------------------------------
# Base corrector system prompt
# ---------------------------------------------------------------------------

BASE_CORRECTOR_SYSTEM = (
    "You are an expert debugger embedded in an AI coding agent. "
    "You receive the original task, the agent's previous output, and the "
    "exact error it caused. "
    "Produce ONLY the corrected output â€” no explanation, no markdown fences, "
    "no preamble. The output will be written directly to a file or executed."
)


# ---------------------------------------------------------------------------
# Syntax error correction
# ---------------------------------------------------------------------------


def syntax_correction_prompt(
    original_task: str,
    bad_code: str,
    error: AgentError,
) -> tuple[str, str]:
    """
    Prompt the LLM to fix a Python/JS syntax error in `bad_code`.

    Returns (system_prompt, user_message).
    """
    system = BASE_CORRECTOR_SYSTEM + (
        " The output must be valid, parseable source code. "
        "Do not change logic beyond what is needed to fix the syntax error."
    )
    user = (
        f"## Original task\n{original_task}\n\n"
        f"## Code that raised a syntax error\n```\n{bad_code}\n```\n\n"
        f"## Syntax error\n```\n{error.message}\n```\n\n"
        "Provide the corrected code:"
    )
    return system, user


# ---------------------------------------------------------------------------
# Test failure correction
# ---------------------------------------------------------------------------


def test_failure_correction_prompt(
    original_task: str,
    current_code: str,
    test_output: str,
    file_path: str = "",
) -> tuple[str, str]:
    """
    Prompt the LLM to fix code that is causing test failures.

    Returns (system_prompt, user_message).
    """
    system = BASE_CORRECTOR_SYSTEM + (
        " The corrected code must make all failing tests pass without "
        "modifying the tests themselves. Preserve the public API."
    )
    file_context = f"**File:** `{file_path}`\n" if file_path else ""
    user = (
        f"## Original task\n{original_task}\n\n"
        f"{file_context}"
        f"## Current code\n```python\n{current_code}\n```\n\n"
        f"## Test output (failures)\n```\n{test_output}\n```\n\n"
        "Provide the corrected code that makes all tests pass:"
    )
    return system, user


# ---------------------------------------------------------------------------
# Tool argument correction
# ---------------------------------------------------------------------------


def tool_argument_correction_prompt(
    tool_name: str,
    bad_arguments: dict,
    validation_errors: list[str],
    tool_description: str = "",
) -> tuple[str, str]:
    """
    Prompt the LLM to fix malformed tool arguments.

    Returns (system_prompt, user_message).
    """
    import json

    system = (
        "You are fixing malformed tool call arguments. "
        "Return ONLY a valid JSON object with the corrected arguments. "
        "No explanation, no markdown fences."
    )
    desc_line = f"\n**Tool description:** {tool_description}" if tool_description else ""
    user = (
        f"## Tool call that failed validation\n"
        f"**Tool:** `{tool_name}`{desc_line}\n\n"
        f"**Arguments provided**\n```json\n{json.dumps(bad_arguments, indent=2)}\n```\n\n"
        f"**Validation errors**\n"
        + "\n".join(f"- {e}" for e in validation_errors)
        + "\n\nProvide corrected JSON arguments:"
    )
    return system, user


# ---------------------------------------------------------------------------
# Goal drift recovery
# ---------------------------------------------------------------------------


def goal_recovery_prompt(
    original_task: str,
    current_reasoning: str,
    steps_taken: int,
) -> tuple[str, str]:
    """
    Prompt the LLM to re-anchor on the original goal after drift.

    Returns (system_prompt, user_message).
    """
    system = (
        "You are reviewing an AI agent's reasoning to detect goal drift. "
        "The agent has lost focus on the original task. "
        "Produce a concise PLAN block that re-anchors it on the original goal. "
        "Format:\nPLAN:\n- Step 1: ...\n- Step 2: ...\n\nREASONING:\n<one paragraph>"
    )
    user = (
        f"## Original task\n{original_task}\n\n"
        f"## Agent's current reasoning (after {steps_taken} steps)\n"
        f"{current_reasoning}\n\n"
        "The agent appears to have drifted from its goal. "
        "Provide a PLAN block that refocuses it on completing the original task:"
    )
    return system, user


# ---------------------------------------------------------------------------
# Context compression prompt
# ---------------------------------------------------------------------------


def context_compression_prompt(
    full_history: str,
    original_task: str,
    max_output_tokens: int = 800,
) -> tuple[str, str]:
    """
    Prompt the LLM to summarise a long conversation history to fit within
    the context window when ContextLengthError is raised.

    Returns (system_prompt, user_message).
    """
    system = (
        "You are compressing an AI agent's reasoning history. "
        f"Produce a summary of at most {max_output_tokens} tokens that preserves: "
        "(1) what has been completed, "
        "(2) what failed and why, "
        "(3) the current state of relevant files. "
        "Omit all successful tool outputs that are no longer needed. "
        "Write in third person, past tense."
    )
    user = (
        f"## Original task\n{original_task}\n\n"
        f"## Full agent history (too long for context window)\n"
        f"{full_history}\n\n"
        "Provide a compressed summary:"
    )
    return system, user
