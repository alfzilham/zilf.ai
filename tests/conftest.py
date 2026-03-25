"""
Shared pytest fixtures for the Zilf AI test suite.

Available fixtures:
  mock_llm           â€” MockLLM with scripted responses
  tmp_workspace      â€” isolated temp dir, sets AGENT_WORKSPACE env var
  registry           â€” ToolRegistry with all default tools
  agent              â€” Agent wired with MockLLM + registry
  error_reporter     â€” ErrorReporter writing to a temp log dir
  checkpoint_manager â€” CheckpointManager in a temp dir
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest


# ---------------------------------------------------------------------------
# MockLLM fixture
# ---------------------------------------------------------------------------


class MockLLM:
    """
    Scripted LLM for testing.

    Pass a list of response strings; each call to generate() or
    generate_text() returns the next one in order.
    When exhausted, always returns a TASK COMPLETE response.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = responses or []
        self._index = 0
        self.call_count = 0

    def _next(self) -> str:
        self.call_count += 1
        if self._index < len(self._responses):
            r = self._responses[self._index]
            self._index += 1
            return r
        return (
            "TASK COMPLETE\nStatus: success\n"
            "Summary: Task done.\nFiles changed: none\n"
            "Tests: not applicable\nNotes: None"
        )

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> Any:
        from agent.llm.base import LLMResponse
        from agent.core.state import ActionType, ToolCall
        import json, re

        text = self._next()

        if "TASK COMPLETE" in text or "final answer" in text.lower():
            return LLMResponse(
                thought=text,
                action_type=ActionType.FINAL_ANSWER,
                final_answer=text,
                input_tokens=50,
                output_tokens=30,
            )

        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                tool_name = data.pop("tool", data.pop("name", None))
                if tool_name:
                    return LLMResponse(
                        thought=text,
                        action_type=ActionType.TOOL_CALL,
                        tool_calls=[ToolCall(tool_name=tool_name, tool_input=data)],
                        input_tokens=50,
                        output_tokens=40,
                    )
            except json.JSONDecodeError:
                pass

        return LLMResponse(
            thought=text,
            action_type=ActionType.FINAL_ANSWER,
            final_answer=text,
            input_tokens=50,
            output_tokens=30,
        )

    async def generate_text(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        return self._next()

    async def stream(self, messages: list[dict], **kwargs: Any):  # type: ignore
        for char in self._next():
            yield char


@pytest.fixture
def mock_llm() -> MockLLM:
    """MockLLM with no scripted responses (always returns TASK COMPLETE)."""
    return MockLLM()


@pytest.fixture
def mock_llm_factory():
    """Factory for MockLLM with custom scripted responses."""
    def _factory(responses: list[str]) -> MockLLM:
        return MockLLM(responses)
    return _factory


# ---------------------------------------------------------------------------
# Temp workspace fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Isolated temporary workspace directory.

    Sets the AGENT_WORKSPACE environment variable so that filesystem
    tools resolve paths relative to this temp dir.
    Cleans up automatically after the test.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    old_env = os.environ.get("AGENT_WORKSPACE")
    os.environ["AGENT_WORKSPACE"] = str(workspace)

    # Force the filesystem module to re-resolve WORKSPACE_ROOT
    import agent.tools.filesystem as fs_module
    old_root = fs_module.WORKSPACE_ROOT
    fs_module.WORKSPACE_ROOT = workspace.resolve()

    yield workspace

    # Restore
    fs_module.WORKSPACE_ROOT = old_root
    if old_env is None:
        os.environ.pop("AGENT_WORKSPACE", None)
    else:
        os.environ["AGENT_WORKSPACE"] = old_env


# ---------------------------------------------------------------------------
# ToolRegistry fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    """ToolRegistry loaded with all default tools."""
    from agent.tools.registry import ToolRegistry
    return ToolRegistry.default()


# ---------------------------------------------------------------------------
# Agent fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def agent(mock_llm, registry):
    """Agent wired with MockLLM and default registry."""
    from agent.core.agent import Agent
    return Agent(
        llm=mock_llm,
        tool_registry=registry,
        use_planner=False,
        verbose=False,
    )


# ---------------------------------------------------------------------------
# Error reporter fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def error_reporter(tmp_path: Path):
    """ErrorReporter writing to a temp log directory."""
    from agent.output.error_reporter import ErrorReporter
    return ErrorReporter(log_dir=str(tmp_path / "logs"), task_id="test_task")


# ---------------------------------------------------------------------------
# Checkpoint manager fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def checkpoint_manager(tmp_path: Path):
    """CheckpointManager using a temp directory."""
    from agent.core.checkpoint import CheckpointManager
    return CheckpointManager(base_dir=str(tmp_path / "checkpoints"))


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


def run_async(coro):
    """Run a coroutine synchronously â€” useful in sync test helpers."""
    return asyncio.get_event_loop().run_until_complete(coro)
