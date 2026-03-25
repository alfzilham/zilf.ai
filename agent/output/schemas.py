"""
Structured output schemas for the Zilf AI.

Pydantic models for:
  - CodeResult        : result of a code generation or edit task
  - TaskResult        : final outcome of a complete task run
  - AgentResponseSchema: top-level response returned to the caller
  - CodingLoopState   : tracks Write â†’ Run â†’ Read â†’ Fix iterations
  - TaskStep          : one step in a dependency-aware plan
  - TaskPlan          : full plan with dependency graph + next-step resolver
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


class TestStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"


# ---------------------------------------------------------------------------
# Code result
# ---------------------------------------------------------------------------


class CodeResult(BaseModel):
    """Result of a code generation, edit, or fix operation."""

    file_path: str
    language: str = "python"
    content: str
    lines_added: int = 0
    lines_removed: int = 0
    test_status: TestStatus = TestStatus.NOT_APPLICABLE
    test_output: str = ""
    iteration: int = Field(1, description="Which coding loop iteration produced this")
    notes: str = ""


# ---------------------------------------------------------------------------
# Coding loop state  (Write â†’ Run â†’ Read â†’ Fix)
# ---------------------------------------------------------------------------


class CodingLoopState(BaseModel):
    """
    Tracks one iteration of the Write â†’ Run â†’ Read â†’ Fix cycle.

    The reasoning loop creates one CodingLoopState per coding subtask
    and updates it as the agent writes, tests, and patches code.
    """

    iteration: int = 0
    code: str = ""
    test_results: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    fixed: bool = False
    files_written: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.fixed and len(self.errors) == 0

    def record_write(self, path: str, content: str) -> None:
        self.iteration += 1
        self.code = content
        if path not in self.files_written:
            self.files_written.append(path)

    def record_test_run(self, output: str) -> None:
        self.test_results.append(output)
        fail_signals = ("FAILED", "ERROR", "AssertionError", "exit code 1")
        self.errors = [
            line for line in output.splitlines()
            if any(s in line for s in fail_signals)
        ]
        self.fixed = len(self.errors) == 0 and bool(output)


# ---------------------------------------------------------------------------
# Task step + plan  (from Pydantic Structured Output.md)
# ---------------------------------------------------------------------------


class TaskStep(BaseModel):
    """A single atomic step in a dependency-aware task plan."""

    step_id: int = Field(..., description="Unique step identifier")
    title: str = Field(..., description="Short step title")
    description: str = Field(..., description="What needs to be done")
    estimated_effort: str = Field("unknown", description="e.g. '30 minutes'")
    dependencies: list[int] = Field(
        default_factory=list,
        description="step_ids that must complete before this step",
    )
    required_tools: list[str] = Field(
        default_factory=list,
        description="Tool names needed",
    )
    status: str = Field("pending", description="pending | running | done | failed | skipped")
    result_summary: str = ""

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        allowed = {"pending", "running", "done", "failed", "skipped"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}, got {v!r}")
        return v


class TaskPlan(BaseModel):
    """
    Dependency-aware plan produced by TaskPlanner.

    Tracks which steps are done and computes which steps can run next.
    """

    task_id: str
    original_task: str
    steps: list[TaskStep] = Field(default_factory=list)
    estimated_total_time: str = "unknown"
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def get_next_steps(self, completed_ids: Optional[list[int]] = None) -> list[TaskStep]:
        """Return steps that are ready to start given the set of completed step IDs."""
        done = set(completed_ids or [])
        return [
            s for s in self.steps
            if s.status == "pending"
            and all(dep in done for dep in s.dependencies)
        ]

    @property
    def completed_steps(self) -> list[TaskStep]:
        return [s for s in self.steps if s.status == "done"]

    @property
    def is_complete(self) -> bool:
        return all(s.status in ("done", "skipped") for s in self.steps)

    @property
    def is_blocked(self) -> bool:
        return any(s.status == "failed" for s in self.steps)

    def mark_done(self, step_id: int, summary: str = "") -> None:
        for s in self.steps:
            if s.step_id == step_id:
                s.status = "done"
                s.result_summary = summary

    def mark_failed(self, step_id: int, reason: str = "") -> None:
        for s in self.steps:
            if s.step_id == step_id:
                s.status = "failed"
                s.result_summary = reason

    def to_summary(self) -> str:
        done = len(self.completed_steps)
        total = len(self.steps)
        lines = [f"Plan: {done}/{total} steps done"]
        icons = {"done": "âœ“", "failed": "âœ—", "running": "â–¶", "pending": "â—‹", "skipped": "â€”"}
        for s in self.steps:
            lines.append(f"  {icons.get(s.status,'?')} {s.step_id}. {s.title}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Task result
# ---------------------------------------------------------------------------


class TaskResult(BaseModel):
    """Final structured result of a complete agent task run."""

    task_id: str
    original_task: str
    status: TaskStatus
    summary: str
    files_changed: list[str] = Field(default_factory=list)
    code_results: list[CodeResult] = Field(default_factory=list)
    test_status: TestStatus = TestStatus.NOT_APPLICABLE
    test_output: str = ""
    steps_taken: int = 0
    total_tokens: int = 0
    duration_seconds: Optional[float] = None
    notes: str = ""
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    def to_task_complete_block(self) -> str:
        files = ", ".join(self.files_changed) if self.files_changed else "none"
        return (
            f"TASK COMPLETE\n"
            f"Status: {self.status.value}\n"
            f"Summary: {self.summary}\n"
            f"Files changed: {files}\n"
            f"Tests: {self.test_status.value}\n"
            f"Notes: {self.notes or 'None'}"
        )


# ---------------------------------------------------------------------------
# Top-level agent response schema
# ---------------------------------------------------------------------------


class AgentResponseSchema(BaseModel):
    """Serialisable version of AgentResponse for API / storage."""

    run_id: str
    task: str
    status: str
    final_answer: Optional[str] = None
    error: Optional[str] = None
    task_result: Optional[TaskResult] = None
    steps_taken: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    @property
    def success(self) -> bool:
        return self.status == "complete"

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens
