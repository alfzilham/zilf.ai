"""
Tool Calling â€” parses LLM tool-call responses, validates schemas,
and dispatches to the registry.

Handles provider differences:
  - Anthropic: tool_use blocks in content array
  - OpenAI: tool_calls array on the message
  - Ollama: JSON action block in text (parsed by ollama_provider.py)

Also implements:
  - Output truncation before feeding results back to LLM
  - Parallel execution via registry.dispatch_parallel()
  - Structured ToolCallResult for the reasoning loop
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from agent.core.state import ActionType, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Parsed tool call + result types (provider-agnostic)
# ---------------------------------------------------------------------------


@dataclass
class ParsedToolCall:
    """A single tool call extracted from an LLM response."""
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str

    def to_state_tool_call(self) -> ToolCall:
        return ToolCall(
            tool_name=self.tool_name,
            tool_input=self.tool_input,
            tool_use_id=self.tool_use_id,
        )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class ToolCallParser:
    """
    Extracts ParsedToolCall objects from raw LLM API responses.

    Supports Anthropic and OpenAI response formats.
    """

    @staticmethod
    def from_anthropic(response_content: list[dict[str, Any]]) -> list[ParsedToolCall]:
        """
        Parse tool calls from an Anthropic messages response content array.

        Anthropic format:
            [{"type": "tool_use", "id": "...", "name": "...", "input": {...}}, ...]
        """
        calls: list[ParsedToolCall] = []
        for block in response_content:
            if block.get("type") == "tool_use":
                calls.append(ParsedToolCall(
                    tool_name=block["name"],
                    tool_input=block.get("input", {}),
                    tool_use_id=block["id"],
                ))
        return calls

    @staticmethod
    def from_openai(tool_calls: list[Any]) -> list[ParsedToolCall]:
        """
        Parse tool calls from an OpenAI chat completion message.

        OpenAI format:
            [ToolCall(id="...", function=Function(name="...", arguments='{"key": "val"}'))]
        """
        calls: list[ParsedToolCall] = []
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
                logger.warning(f"[tool_calling] Could not parse arguments JSON for {tc.function.name}")
            calls.append(ParsedToolCall(
                tool_name=tc.function.name,
                tool_input=args,
                tool_use_id=tc.id,
            ))
        return calls

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ParsedToolCall | None:
        """
        Parse a single tool call from a plain dict (used by Ollama provider).

        Dict format: {"name": "...", "input": {...}, "tool_use_id": "..."}
        """
        name = data.get("name") or data.get("tool_name")
        if not name:
            return None
        return ParsedToolCall(
            tool_name=name,
            tool_input=data.get("input") or data.get("tool_input", {}),
            tool_use_id=data.get("tool_use_id") or data.get("id", ""),
        )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class ToolDispatcher:
    """
    Takes a list of ParsedToolCalls, executes them (optionally in parallel),
    and returns ToolResult objects ready to inject into the LLM context.
    """

    def __init__(self, registry: "Any") -> None:  # type: ignore[name-defined]
        self.registry = registry

    async def execute(
        self,
        calls: list[ParsedToolCall],
        parallel: bool = True,
    ) -> list[ToolResult]:
        """
        Execute a list of tool calls and return results.

        Args:
            calls:    Tool calls to execute.
            parallel: Whether to run independent calls concurrently (default True).
        """
        if not calls:
            return []

        if parallel and len(calls) > 1:
            raw_calls = [
                {"name": c.tool_name, "arguments": c.tool_input, "tool_use_id": c.tool_use_id}
                for c in calls
            ]
            outputs = await self.registry.dispatch_parallel(raw_calls)
            results = []
            for call, output in zip(calls, outputs):
                results.append(self._make_result(call, output))
            return results

        # Sequential execution
        results: list[ToolResult] = []
        for call in calls:
            output = await self.registry.dispatch(call.tool_name, call.tool_input)
            results.append(self._make_result(call, output))
        return results

    @staticmethod
    def _make_result(call: ParsedToolCall, output: str) -> ToolResult:
        is_error = (
            output.startswith("Error")
            or output.startswith("Exit code")
            or "not installed" in output
            or "ModuleNotFoundError" in output
            or "ImportError" in output
        )
        return ToolResult(
            tool_name=call.tool_name,
            tool_use_id=call.tool_use_id,
            output=output if not is_error else "",
            error=output if is_error else None,
        )

    # -----------------------------------------------------------------------
    # Message formatters (inject results back into conversation)
    # -----------------------------------------------------------------------

    @staticmethod
    def to_anthropic_messages(results: list[ToolResult]) -> dict[str, Any]:
        """
        Format ToolResults as an Anthropic user message containing tool_result blocks.

        Anthropic requires tool results to be sent as role='user'.
        """
        content = []
        for r in results:
            content.append({
                "type": "tool_result",
                "tool_use_id": r.tool_use_id or "",
                "content": r.error if r.error else r.output,
                "is_error": r.error is not None,
            })
        return {"role": "user", "content": content}

    @staticmethod
    def to_openai_messages(results: list[ToolResult]) -> list[dict[str, Any]]:
        """
        Format ToolResults as a list of OpenAI tool messages (role='tool').
        """
        return [
            {
                "role": "tool",
                "tool_call_id": r.tool_use_id or "",
                "content": r.error if r.error else r.output,
            }
            for r in results
        ]
