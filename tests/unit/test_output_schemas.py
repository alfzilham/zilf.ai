"""
Unit tests for Pydantic output schemas and the LLM output parser.

Tests:
  - TaskStep / TaskPlan dependency graph
  - CodingLoopState Writeâ†’Runâ†’Fix cycle
  - TaskResult.to_task_complete_block() format
  - AgentResponseSchema success/token properties
  - LLMOutputParser edge cases
  - Exception hierarchy and to_dict()
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# TaskStep / TaskPlan
# ---------------------------------------------------------------------------


class TestTaskPlan:

    def _make_plan(self):
        from agent.output.schemas import TaskPlan, TaskStep
        return TaskPlan(
            task_id="p1",
            original_task="Build an API",
            steps=[
                TaskStep(step_id=1, title="Setup", description="Init project", dependencies=[]),
                TaskStep(step_id=2, title="Implement", description="Write code", dependencies=[1]),
                TaskStep(step_id=3, title="Test", description="Run tests", dependencies=[2]),
                TaskStep(step_id=4, title="Deploy", description="Push to prod", dependencies=[2, 3]),
            ],
        )

    def test_step_1_ready_initially(self) -> None:
        plan = self._make_plan()
        ready = plan.get_next_steps([])
        assert len(ready) == 1
        assert ready[0].step_id == 1

    def test_step_2_unlocks_after_step_1(self) -> None:
        plan = self._make_plan()
        plan.mark_done(1)
        ready = plan.get_next_steps([1])
        assert any(s.step_id == 2 for s in ready)

    def test_step_4_requires_both_2_and_3(self) -> None:
        plan = self._make_plan()
        plan.mark_done(1)
        plan.mark_done(2)
        ready = plan.get_next_steps([1, 2])
        ids = [s.step_id for s in ready]
        assert 3 in ids
        assert 4 not in ids  # still needs step 3

    def test_plan_not_complete_until_all_done(self) -> None:
        plan = self._make_plan()
        plan.mark_done(1)
        plan.mark_done(2)
        assert not plan.is_complete

    def test_plan_complete_when_all_done(self) -> None:
        plan = self._make_plan()
        for sid in [1, 2, 3, 4]:
            plan.mark_done(sid)
        assert plan.is_complete

    def test_is_blocked_when_step_fails(self) -> None:
        plan = self._make_plan()
        plan.mark_failed(2, "compile error")
        assert plan.is_blocked

    def test_to_summary_shows_status_icons(self) -> None:
        plan = self._make_plan()
        plan.mark_done(1)
        summary = plan.to_summary()
        assert "âœ“" in summary
        assert "â—‹" in summary

    def test_invalid_status_raises(self) -> None:
        from agent.output.schemas import TaskStep
        with pytest.raises(Exception):
            TaskStep(step_id=1, title="X", description="Y", status="invalid_status")


# ---------------------------------------------------------------------------
# CodingLoopState
# ---------------------------------------------------------------------------


class TestCodingLoopState:

    def test_record_write_increments_iteration(self) -> None:
        from agent.output.schemas import CodingLoopState
        s = CodingLoopState()
        s.record_write("/workspace/main.py", "def main(): pass")
        assert s.iteration == 1
        assert "/workspace/main.py" in s.files_written

    def test_record_write_deduplicates_paths(self) -> None:
        from agent.output.schemas import CodingLoopState
        s = CodingLoopState()
        s.record_write("/workspace/a.py", "v1")
        s.record_write("/workspace/a.py", "v2")
        assert s.files_written.count("/workspace/a.py") == 1

    def test_record_test_run_detects_failures(self) -> None:
        from agent.output.schemas import CodingLoopState
        s = CodingLoopState()
        s.record_test_run("FAILED test_auth - AssertionError\n2 failed, 1 passed")
        assert len(s.errors) > 0
        assert not s.fixed

    def test_record_test_run_marks_fixed_on_pass(self) -> None:
        from agent.output.schemas import CodingLoopState
        s = CodingLoopState()
        s.record_test_run("3 passed in 0.5s")
        assert len(s.errors) == 0
        assert s.fixed

    def test_is_complete_requires_fixed_and_no_errors(self) -> None:
        from agent.output.schemas import CodingLoopState
        s = CodingLoopState()
        assert not s.is_complete
        s.record_test_run("5 passed")
        assert s.is_complete


# ---------------------------------------------------------------------------
# TaskResult
# ---------------------------------------------------------------------------


class TestTaskResult:

    def test_to_task_complete_block_format(self) -> None:
        from agent.output.schemas import TaskResult, TaskStatus, TestStatus
        r = TaskResult(
            task_id="r1",
            original_task="Fix the bug",
            status=TaskStatus.SUCCESS,
            summary="Fixed null pointer in auth.py",
            files_changed=["src/auth.py"],
            test_status=TestStatus.PASSED,
        )
        block = r.to_task_complete_block()
        assert "TASK COMPLETE" in block
        assert "success" in block
        assert "auth.py" in block
        assert "passed" in block

    def test_empty_files_shows_none(self) -> None:
        from agent.output.schemas import TaskResult, TaskStatus
        r = TaskResult(
            task_id="r2",
            original_task="Research task",
            status=TaskStatus.SUCCESS,
            summary="Done",
        )
        block = r.to_task_complete_block()
        assert "none" in block


# ---------------------------------------------------------------------------
# AgentResponseSchema
# ---------------------------------------------------------------------------


class TestAgentResponseSchema:

    def test_success_property(self) -> None:
        from agent.output.schemas import AgentResponseSchema
        r = AgentResponseSchema(run_id="x", task="t", status="complete")
        assert r.success

    def test_not_success_when_failed(self) -> None:
        from agent.output.schemas import AgentResponseSchema
        r = AgentResponseSchema(run_id="x", task="t", status="failed")
        assert not r.success

    def test_total_tokens_sum(self) -> None:
        from agent.output.schemas import AgentResponseSchema
        r = AgentResponseSchema(
            run_id="x", task="t", status="complete",
            total_input_tokens=100, total_output_tokens=50,
        )
        assert r.total_tokens == 150


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptions:

    def test_rate_limit_error_carries_retry_after(self) -> None:
        from agent.core.exceptions import RateLimitError
        e = RateLimitError(retry_after=30.0)
        assert e.retry_after == 30.0
        assert e.recoverable is True

    def test_content_policy_not_recoverable(self) -> None:
        from agent.core.exceptions import ContentPolicyError
        e = ContentPolicyError(policy_code="violence")
        assert e.recoverable is False

    def test_disk_full_not_recoverable(self) -> None:
        from agent.core.exceptions import DiskFullError
        e = DiskFullError()
        assert e.recoverable is False

    def test_to_dict_has_required_keys(self) -> None:
        from agent.core.exceptions import SubprocessToolError
        e = SubprocessToolError("run_cmd", returncode=1, stderr="build failed")
        d = e.to_dict()
        assert {"error_class", "message", "recoverable", "context", "timestamp"} <= d.keys()

    def test_syntax_tool_error_captures_line(self) -> None:
        from agent.core.exceptions import SyntaxToolError
        e = SyntaxToolError("write_file", line=42, detail="invalid syntax")
        assert e.line == 42
        assert "42" in e.message

    def test_infinite_loop_error_carries_step(self) -> None:
        from agent.core.exceptions import InfiniteLoopError
        e = InfiniteLoopError(loop_length=3, step=15)
        assert e.step == 15
        assert e.loop_length == 3
