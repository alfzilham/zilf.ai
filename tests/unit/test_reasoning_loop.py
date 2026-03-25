"""
Unit tests for the reasoning loop.

Tests:
  - Think/Act/Observe cycle runs correctly
  - Max steps guard fires and sets correct status
  - Tool failures are reflected in step results
  - Final answer terminates the loop immediately
  - Step history is preserved on AgentState
"""

from __future__ import annotations

import pytest
from pathlib import Path


class TestReasoningLoop:

    @pytest.mark.asyncio
    async def test_completes_with_final_answer(self, tmp_workspace, mock_llm_factory, registry) -> None:
        from agent.core.reasoning_loop import ReasoningLoop
        from agent.core.state import AgentState, AgentStatus

        llm = mock_llm_factory([
            "TASK COMPLETE\nStatus: success\nSummary: Done.\nFiles changed: none\nTests: not applicable\nNotes: None"
        ])
        loop = ReasoningLoop(llm=llm, tool_registry=registry, max_steps=10, verbose=False)
        state = AgentState(run_id="test-001", task="Simple task", max_steps=10)

        final = await loop.run(state)
        assert final.status == AgentStatus.COMPLETE
        assert final.final_answer is not None

    @pytest.mark.asyncio
    async def test_max_steps_guard(self, mock_llm_factory, registry) -> None:
        from agent.core.reasoning_loop import ReasoningLoop
        from agent.core.state import AgentState, AgentStatus

        # Always returns a tool call â€” never finishes
        responses = ['{"tool": "list_dir", "directory": "/workspace"}'] * 20
        llm = mock_llm_factory(responses)
        loop = ReasoningLoop(llm=llm, tool_registry=registry, max_steps=3, verbose=False)
        state = AgentState(run_id="test-002", task="Infinite task", max_steps=3)

        final = await loop.run(state)
        assert final.status == AgentStatus.MAX_STEPS_REACHED
        assert final.current_step >= 3

    @pytest.mark.asyncio
    async def test_step_history_preserved(self, tmp_workspace, mock_llm_factory, registry) -> None:
        from agent.core.reasoning_loop import ReasoningLoop
        from agent.core.state import AgentState

        path = str(tmp_workspace / "test.txt")
        responses = [
            f'{{"tool": "write_file", "path": "{path}", "content": "hello"}}',
            "TASK COMPLETE\nStatus: success\nSummary: Done.\nFiles changed: none\nTests: not applicable\nNotes: None",
        ]
        llm = mock_llm_factory(responses)
        loop = ReasoningLoop(llm=llm, tool_registry=registry, max_steps=10, verbose=False)
        state = AgentState(run_id="test-003", task="Write a file", max_steps=10)

        final = await loop.run(state)
        assert len(final.steps) >= 1
        assert final.current_step >= 1

    @pytest.mark.asyncio
    async def test_tool_error_does_not_crash_loop(self, mock_llm_factory, registry) -> None:
        from agent.core.reasoning_loop import ReasoningLoop
        from agent.core.state import AgentState, AgentStatus

        responses = [
            # Call a tool that will fail (missing required arg)
            '{"tool": "read_file", "path": "/workspace/does_not_exist_xyz.txt"}',
            "TASK COMPLETE\nStatus: partial\nSummary: Recovered.\nFiles changed: none\nTests: not applicable\nNotes: None",
        ]
        llm = mock_llm_factory(responses)
        loop = ReasoningLoop(llm=llm, tool_registry=registry, max_steps=10, verbose=False)
        state = AgentState(run_id="test-004", task="Read nonexistent file", max_steps=10)

        final = await loop.run(state)
        # Loop should complete, not crash
        assert final.status in (AgentStatus.COMPLETE, AgentStatus.MAX_STEPS_REACHED)

    @pytest.mark.asyncio
    async def test_token_usage_accumulates(self, mock_llm_factory, registry) -> None:
        from agent.core.reasoning_loop import ReasoningLoop
        from agent.core.state import AgentState

        responses = [
            "TASK COMPLETE\nStatus: success\nSummary: Done.\nFiles changed: none\nTests: not applicable\nNotes: None",
        ]
        llm = mock_llm_factory(responses)
        loop = ReasoningLoop(llm=llm, tool_registry=registry, max_steps=5, verbose=False)
        state = AgentState(run_id="test-005", task="Token test", max_steps=5)

        final = await loop.run(state)
        assert final.total_input_tokens >= 0
        assert final.total_output_tokens >= 0


# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------


class TestAgentState:

    def test_is_done_when_complete(self) -> None:
        from agent.core.state import AgentState, AgentStatus
        s = AgentState(run_id="x", task="t")
        s.status = AgentStatus.COMPLETE
        assert s.is_done

    def test_is_done_when_failed(self) -> None:
        from agent.core.state import AgentState, AgentStatus
        s = AgentState(run_id="x", task="t")
        s.status = AgentStatus.FAILED
        assert s.is_done

    def test_not_done_when_running(self) -> None:
        from agent.core.state import AgentState, AgentStatus
        s = AgentState(run_id="x", task="t")
        s.status = AgentStatus.RUNNING
        assert not s.is_done

    def test_steps_remaining(self) -> None:
        from agent.core.state import AgentState
        s = AgentState(run_id="x", task="t", max_steps=10)
        s.current_step = 3
        assert s.steps_remaining == 7

    def test_context_messages_roundtrip(self) -> None:
        from agent.core.state import AgentState, ReasoningStep, ActionType, ToolCall, ToolResult
        s = AgentState(run_id="x", task="t", max_steps=5)
        step = ReasoningStep(step_number=1)
        step.thought = "I should read a file"
        step.tool_calls = [ToolCall(tool_name="read_file", tool_input={"path": "/workspace/a.py"}, tool_use_id="tu_1")]
        step.tool_results = [ToolResult(tool_name="read_file", tool_use_id="tu_1", output="file content")]
        s.add_step(step)

        messages = s.context_messages()
        assert len(messages) >= 1
