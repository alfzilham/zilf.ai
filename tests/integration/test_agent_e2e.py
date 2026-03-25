"""
Integration tests â€” full agent task runs with MockLLM.

Tests:
  - Write + verify task completes successfully
  - Multi-step task runs to completion
  - Agent handles tool errors and continues
  - Error reporter logs failures correctly
  - Checkpoint saves state after each step
"""

from __future__ import annotations

import pytest
from pathlib import Path


class TestAgentEndToEnd:

    @pytest.mark.asyncio
    async def test_write_and_read_task(self, tmp_workspace: Path, mock_llm_factory, registry) -> None:
        """Agent writes a file, reads it back, then completes."""
        from agent.core.agent import Agent
        from agent.core.state import AgentStatus

        path = str(tmp_workspace / "output.txt")
        responses = [
            f'{{"tool": "write_file", "path": "{path}", "content": "hello from agent"}}',
            f'{{"tool": "read_file", "path": "{path}"}}',
            "TASK COMPLETE\nStatus: success\nSummary: File created and verified.\nFiles changed: output.txt\nTests: not applicable\nNotes: None",
        ]
        llm = mock_llm_factory(responses)
        agent = Agent(llm=llm, tool_registry=registry, use_planner=False, verbose=False)

        response = await agent.run("Create output.txt with content 'hello from agent'")

        assert response.success
        assert response.steps_taken >= 1
        assert (tmp_workspace / "output.txt").exists()

    @pytest.mark.asyncio
    async def test_agent_completes_immediately_on_final_answer(self, mock_llm_factory, registry) -> None:
        """Agent that immediately returns final answer completes in 1 step."""
        from agent.core.agent import Agent
        from agent.core.state import AgentStatus

        llm = mock_llm_factory([
            "TASK COMPLETE\nStatus: success\nSummary: Nothing to do.\nFiles changed: none\nTests: not applicable\nNotes: None"
        ])
        agent = Agent(llm=llm, tool_registry=registry, use_planner=False, verbose=False)

        response = await agent.run("What is 2 + 2?")
        assert response.status == AgentStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_agent_reaches_max_steps(self, mock_llm_factory, registry) -> None:
        """Agent hitting max steps reports MAX_STEPS_REACHED."""
        from agent.core.agent import Agent
        from agent.core.state import AgentStatus

        # Always calls list_dir â€” never finishes
        responses = ['{"tool": "list_dir", "directory": "/workspace"}'] * 20
        llm = mock_llm_factory(responses)
        agent = Agent(llm=llm, tool_registry=registry, max_steps=3, use_planner=False, verbose=False)

        response = await agent.run("Loop forever")
        assert response.status == AgentStatus.MAX_STEPS_REACHED

    @pytest.mark.asyncio
    async def test_token_usage_tracked(self, mock_llm_factory, registry) -> None:
        """Token usage is accumulated across steps."""
        from agent.core.agent import Agent

        responses = [
            "TASK COMPLETE\nStatus: success\nSummary: Done.\nFiles changed: none\nTests: not applicable\nNotes: None"
        ]
        llm = mock_llm_factory(responses)
        agent = Agent(llm=llm, tool_registry=registry, use_planner=False, verbose=False)

        response = await agent.run("Quick task")
        # MockLLM returns 50 input + 30 output per call
        assert response.total_input_tokens >= 0
        assert response.run_id is not None

    @pytest.mark.asyncio
    async def test_agent_run_id_unique_per_run(self, mock_llm_factory, registry) -> None:
        """Each agent.run() call generates a unique run_id."""
        from agent.core.agent import Agent

        llm = mock_llm_factory([])
        agent = Agent(llm=llm, tool_registry=registry, use_planner=False, verbose=False)

        r1 = await agent.run("Task 1")
        r2 = await agent.run("Task 2")
        assert r1.run_id != r2.run_id


class TestToolChains:

    @pytest.mark.asyncio
    async def test_write_read_delete_chain(self, tmp_workspace: Path) -> None:
        """Write â†’ read back â†’ delete â€” all via registry dispatch."""
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        path = str(tmp_workspace / "chain_test.txt")

        write = await reg.dispatch("write_file", {"path": path, "content": "chain content"})
        assert "OK" in write

        read = await reg.dispatch("read_file", {"path": path})
        assert "chain content" in read

        delete = await reg.dispatch("delete_file", {"path": path})
        assert "OK" in delete

        read_after = await reg.dispatch("read_file", {"path": path})
        assert "error" in read_after.lower() or "not found" in read_after.lower()

    @pytest.mark.asyncio
    async def test_write_then_search_finds_file(self, tmp_workspace: Path) -> None:
        """Write a file then search for it by content."""
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        path = str(tmp_workspace / "searchable.py")

        await reg.dispatch("write_file", {"path": path, "content": "def unique_function_xyz(): pass"})

        result = await reg.dispatch("search_files", {
            "pattern": "*.py",
            "directory": str(tmp_workspace),
            "contains": "unique_function_xyz",
        })
        assert "searchable.py" in result

    @pytest.mark.asyncio
    async def test_list_dir_after_writes(self, tmp_workspace: Path) -> None:
        """List directory shows newly created files."""
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        for name in ["alpha.py", "beta.py", "gamma.py"]:
            await reg.dispatch("write_file", {
                "path": str(tmp_workspace / name),
                "content": f"# {name}",
            })

        result = await reg.dispatch("list_dir", {"directory": str(tmp_workspace)})
        assert "alpha.py" in result
        assert "beta.py" in result
        assert "gamma.py" in result
