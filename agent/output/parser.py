"""
Pydantic Output Parser — reliable extraction and validation of LLM responses.

Solves the core problem: LLMs produce inconsistent output formats.
This module provides:

1. LLMOutputParser       — extracts JSON from messy LLM text, validates with Pydantic
2. Self-executing tools  — Pydantic models with .execute() methods
   - FileReadTool
   - FileWriteTool
   - RunCommandTool
   - WebSearchTool
   - CodeAnalysisTool
3. SafeFilePath          — field validator preventing path traversal
4. StructuredAgent       — minimal agent that parses + dispatches tool calls

Why Pydantic for tool calls?
  - Type safety at parse time (not at runtime crash)
  - Automatic field validation + helpful error messages
  - Self-documenting schemas the LLM can follow
  - Easy serialisation for audit logs and checkpointing
"""

from __future__ import annotations

import json
import os
import re
import subprocess
# FIX: added ClassVar import
from typing import Any, ClassVar, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from agent.core.exceptions import SyntaxToolError


# ---------------------------------------------------------------------------
# Safe file path validator
# ---------------------------------------------------------------------------


class SafeFilePath(BaseModel):
    """
    File path with security validation.

    Prevents:
      - Directory traversal (..)
      - Absolute paths outside /workspace
      - Null byte injection
    """

    path: str = Field(..., description="File path — relative or absolute under /workspace")

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Path cannot be empty")
        if ".." in v:
            raise ValueError('Path cannot contain ".." (directory traversal)')
        if "\x00" in v:
            raise ValueError("Path cannot contain null bytes")
        # Allow /workspace/* absolute paths; block all others
        if v.startswith("/") and not v.startswith("/workspace"):
            raise ValueError(
                f"Absolute paths must be under /workspace, got: {v!r}"
            )
        return v.strip()


# ---------------------------------------------------------------------------
# Self-executing tool models
# ---------------------------------------------------------------------------


class FileReadTool(BaseModel):
    """Read a file from the workspace."""

    tool: Literal["read_file"] = "read_file"
    file_path: str = Field(..., description="Path to the file to read")
    start_line: Optional[int] = Field(None, description="First line to read (1-indexed)")
    end_line: Optional[int] = Field(None, description="Last line to read (inclusive)")

    @field_validator("file_path")
    @classmethod
    def _safe_path(cls, v: str) -> str:
        SafeFilePath(path=v)   # raises ValueError on traversal
        return v

    def execute(self) -> str:
        try:
            with open(self.file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if self.start_line or self.end_line:
                sl = max(0, (self.start_line or 1) - 1)
                el = self.end_line or len(lines)
                lines = lines[sl:el]
            return "".join(lines)
        except FileNotFoundError:
            return f"Error: file not found: {self.file_path}"
        except Exception as exc:
            return f"Error reading {self.file_path}: {exc}"


class FileWriteTool(BaseModel):
    """Write or overwrite a file in the workspace."""

    tool: Literal["write_file"] = "write_file"
    file_path: str = Field(..., description="Destination path")
    content: str = Field(..., description="Raw text to write — no markdown fences")
    encoding: str = Field("utf-8", description="File encoding")
    validate_python: bool = Field(
        False,
        description="If True, validate Python syntax before writing",
    )

    @field_validator("file_path")
    @classmethod
    def _safe_path(cls, v: str) -> str:
        SafeFilePath(path=v)
        return v

    @model_validator(mode="after")
    def _check_syntax(self) -> "FileWriteTool":
        if self.validate_python and self.file_path.endswith(".py"):
            import ast
            try:
                ast.parse(self.content)
            except SyntaxError as e:
                raise SyntaxToolError(
                    "write_file",
                    line=e.lineno or 0,
                    detail=e.msg,
                    context={"path": self.file_path},
                ) from e
        return self

    def execute(self) -> str:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.file_path)), exist_ok=True)
            with open(self.file_path, "w", encoding=self.encoding) as f:
                f.write(self.content)
            return f"OK: wrote {len(self.content):,} chars to {self.file_path}"
        except Exception as exc:
            return f"Error writing {self.file_path}: {exc}"


class RunCommandTool(BaseModel):
    """Execute a shell command in the workspace."""

    tool: Literal["run_command"] = "run_command"
    command: str = Field(..., description="Shell command string")
    working_directory: str = Field(".", description="Working directory")
    timeout_seconds: int = Field(60, ge=1, le=300, description="Execution timeout")

    # FIX: ClassVar annotation so Pydantic v2 does NOT treat this as a model field
    _FORBIDDEN: ClassVar[re.Pattern[str]] = re.compile(
        r"rm\s+-rf\s+/|chmod\s+777\s+/|curl.*\|\s*bash|wget.*\|\s*sh",
        re.IGNORECASE,
    )

    @field_validator("command")
    @classmethod
    def _check_command(cls, v: str) -> str:
        if cls._FORBIDDEN.search(v):
            raise ValueError(f"Forbidden command pattern detected: {v!r}")
        return v

    def execute(self) -> str:
        import shlex
        try:
            result = subprocess.run(
                shlex.split(self.command),
                cwd=self.working_directory,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                output = f"Exit {result.returncode}\n{result.stderr.strip()}"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {self.timeout_seconds}s"
        except Exception as exc:
            return f"Error running command: {exc}"


class WebSearchTool(BaseModel):
    """Search the web for information."""

    tool: Literal["web_search"] = "web_search"
    query: str = Field(..., description="Search query", min_length=2)
    max_results: int = Field(5, ge=1, le=10)

    def execute(self) -> str:
        # Delegate to the web_search tool at runtime
        import asyncio
        from agent.tools.web_search import web_search
        try:
            return asyncio.run(web_search(self.query, max_results=self.max_results))
        except Exception as exc:
            return f"Search error: {exc}"


class CodeAnalysisTool(BaseModel):
    """Analyse code quality, complexity, or security in a file."""

    tool: Literal["analyze_code"] = "analyze_code"
    file_path: str = Field(..., description="File to analyse")
    analysis_type: Literal["quality", "security", "complexity"] = Field(
        "quality", description="Type of analysis"
    )

    def execute(self) -> str:
        """Run basic static analysis via subprocess (flake8/bandit/radon)."""
        cmd_map = {
            "quality": f"flake8 {self.file_path} --max-line-length=100",
            "security": f"bandit -r {self.file_path} -q",
            "complexity": f"radon cc {self.file_path} -s",
        }
        cmd = cmd_map[self.analysis_type]
        tool = RunCommandTool(tool="run_command", command=cmd)
        result = tool.execute()
        return result or f"No {self.analysis_type} issues found."


# ---------------------------------------------------------------------------
# Tool union — maps tool name → model class
# ---------------------------------------------------------------------------

TOOL_MODELS: dict[str, type[BaseModel]] = {
    "read_file":    FileReadTool,
    "write_file":   FileWriteTool,
    "run_command":  RunCommandTool,
    "web_search":   WebSearchTool,
    "analyze_code": CodeAnalysisTool,
}


# ---------------------------------------------------------------------------
# LLM output parser
# ---------------------------------------------------------------------------


class LLMOutputParser:
    """
    Extracts and validates tool call JSON from raw LLM text.

    Handles the common case where the LLM wraps JSON in markdown fences
    or adds prose before/after the JSON block.

    Usage::

        parser = LLMOutputParser()
        tool = parser.parse_tool_call('{"tool": "read_file", "file_path": "/workspace/main.py"}')
        result = tool.execute()
    """

    @staticmethod
    def extract_json(text: str) -> str | None:
        """
        Extract the first valid JSON object from `text`.

        Tries (in order):
          1. Strip markdown code fences then parse
          2. Find outermost { ... } and parse
          3. Return None if nothing found
        """
        # Strip markdown fences
        clean = re.sub(r"```(?:json)?\n?", "", text).strip()
        clean = re.sub(r"```\s*$", "", clean).strip()

        # Try the whole cleaned text first
        try:
            json.loads(clean)
            return clean
        except json.JSONDecodeError:
            pass

        # FIX: search entire original text for any JSON object, not just clean
        # This handles JSON embedded in prose like "I will do X.\n{...}\nLet me know."
        for match in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}", text, re.DOTALL):
            candidate = match.group(0)
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue

        # Fall back: find outermost braces in cleaned text
        start = clean.find("{")
        end = clean.rfind("}") + 1
        if start != -1 and end > start:
            candidate = clean[start:end]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        return None

    def parse_tool_call(self, llm_output: str) -> BaseModel | None:
        """
        Parse `llm_output` into the appropriate self-executing tool model.

        Returns None if the output doesn't contain a recognisable tool call.
        """
        json_str = self.extract_json(llm_output)
        if json_str is None:
            return None

        try:
            data: dict[str, Any] = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        tool_name = data.get("tool") or data.get("name") or data.get("tool_name")
        if not tool_name or tool_name not in TOOL_MODELS:
            return None

        try:
            if tool_name in llm_output and "final_answer" in llm_output:
                return None
            return TOOL_MODELS[tool_name].model_validate(data)
        except Exception:
            return None

    def parse_or_raise(self, llm_output: str, expected_model: type[BaseModel]) -> BaseModel:
        """
        Parse `llm_output` into `expected_model`, raising ValueError on failure.
        """
        json_str = self.extract_json(llm_output)
        if json_str is None:
            raise ValueError(f"No JSON found in LLM output: {llm_output[:200]!r}")
        data = json.loads(json_str)
        return expected_model.model_validate(data)


# ---------------------------------------------------------------------------
# Structured agent (minimal, for examples/testing)
# ---------------------------------------------------------------------------


class StructuredAgent:
    """
    Minimal agent that uses LLMOutputParser to dispatch tool calls.

    This is the "show everything connected" demonstration agent.
    For production use, use agent.core.agent.Agent instead.
    """

    def __init__(self, llm: Any, max_iterations: int = 10) -> None:
        self.llm = llm
        self.max_iterations = max_iterations
        self.parser = LLMOutputParser()
        self.history: list[dict[str, Any]] = []

    def run(self, task: str) -> str:
        """Execute the task using a ReAct loop with Pydantic tool dispatch."""
        messages = [{"role": "user", "content": self._build_prompt(task)}]

        for iteration in range(self.max_iterations):
            # Call LLM
            response_text = self._call_llm(messages)

            # Check for final answer
            if "final answer" in response_text.lower() or "task complete" in response_text.lower():
                return self._extract_final_answer(response_text)

            # Try to parse as a tool call
            tool = self.parser.parse_tool_call(response_text)

            if tool is None:
                # No tool call — treat as final answer
                return response_text.strip()

            # Execute the tool
            observation = tool.execute()  # type: ignore[attr-defined]

            # Record step
            self.history.append({
                "iteration": iteration,
                "tool": tool.tool,  # type: ignore[attr-defined]
                "observation": observation[:200],
            })

            # Append assistant turn + observation to messages
            messages.append({"role": "assistant", "content": response_text})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}",
            })

        return "Max iterations reached without completing the task."

    def _call_llm(self, messages: list[dict]) -> str:
        """Call the LLM synchronously."""
        import asyncio
        if hasattr(self.llm, "generate_text"):
            # FIX: asyncio.run() instead of deprecated get_event_loop()
            return asyncio.run(self.llm.generate_text(messages))
        # Fallback: call synchronously (MockLLM)
        return self.llm.invoke(str(messages))

    def _build_prompt(self, task: str) -> str:
        tools_desc = "\n".join(
            f"- {name}: {cls.__doc__ or ''}"
            for name, cls in TOOL_MODELS.items()
        )
        return (
            f"You are a coding agent. Complete this task:\n{task}\n\n"
            f"Available tools (respond with JSON):\n{tools_desc}\n\n"
            "For each action, output JSON like:\n"
            '{"tool": "read_file", "file_path": "/workspace/main.py"}\n\n'
            "When done, output: Final Answer: <your answer>"
        )

    def _extract_final_answer(self, text: str) -> str:
        m = re.search(r"(?:final answer|task complete)[:\s]*(.*)", text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else text.strip()