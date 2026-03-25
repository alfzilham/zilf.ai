"""
Pydantic models for agent state management.

Defines the data structures that flow through the entire reasoning loop:
AgentState, TaskPlan, StepResult, ToolCall, and Memory snapshots.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentStatus(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    RUNNING = "running"
    REFLECTING = "reflecting"
    COMPLETE = "complete"
    FAILED = "failed"
    MAX_STEPS_REACHED = "max_steps_reached"


class ActionType(str, Enum):
    TOOL_CALL = "tool_call"
    FINAL_ANSWER = "final_answer"
    ASK_CLARIFICATION = "ask_clarification"
    REFLECTION = "reflection"


# ---------------------------------------------------------------------------
# Tool call & results
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    """A single tool invocation decided by the LLM."""

    tool_name: str = Field(..., description="Name of the tool to invoke")
    tool_input: dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool")
    tool_use_id: str | None = Field(None, description="Provider-assigned ID (Anthropic tool_use)")


class ToolResult(BaseModel):
    """The outcome of a tool invocation."""

    tool_name: str
    tool_use_id: str | None = None
    output: str = Field(..., description="String output from the tool")
    error: str | None = Field(None, description="Error message if the tool failed")
    elapsed_ms: float = Field(0.0, description="Wall-clock time for the call in milliseconds")

    @property
    def success(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Reasoning step (one turn of the ReAct loop)
# ---------------------------------------------------------------------------


class ReasoningStep(BaseModel):
    """
    A single iteration of the Perceive â†’ Reason â†’ Act â†’ Observe â†’ Reflect cycle.

    Maps directly to one LLM call plus its resulting tool executions.
    """

    step_number: int = Field(..., ge=1)
    thought: str = Field("", description="LLM's internal reasoning trace (Think)")
    action_type: ActionType = ActionType.TOOL_CALL
    tool_calls: list[ToolCall] = Field(default_factory=list, description="Tools chosen by the LLM")
    tool_results: list[ToolResult] = Field(default_factory=list, description="Observations from tool calls")
    reflection: str = Field("", description="Post-observation reflection text")
    final_answer: str | None = Field(None, description="Set when action_type == FINAL_ANSWER")
    status: StepStatus = StepStatus.PENDING
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    def mark_complete(self, status: StepStatus = StepStatus.SUCCESS) -> None:
        self.status = status
        self.completed_at = datetime.utcnow()

    @property
    def observations(self) -> str:
        """Concatenated text of all tool results for this step."""
        parts = []
        for r in self.tool_results:
            if r.error:
                parts.append(f"[{r.tool_name} ERROR] {r.error}")
            else:
                parts.append(f"[{r.tool_name}] {r.output}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Task plan
# ---------------------------------------------------------------------------


class SubTask(BaseModel):
    """One atomic piece of a decomposed plan."""

    id: str = Field(..., description="Unique identifier, e.g. 'step_1'")
    title: str
    description: str
    depends_on: list[str] = Field(default_factory=list, description="IDs of prerequisite subtasks")
    status: StepStatus = StepStatus.PENDING
    result_summary: str = ""


class TaskPlan(BaseModel):
    """A structured plan produced by the planning module."""

    task_id: str
    original_task: str
    subtasks: list[SubTask] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    estimated_steps: int = Field(0, description="Rough estimate of ReAct steps needed")

    @property
    def completed_subtasks(self) -> list[SubTask]:
        return [s for s in self.subtasks if s.status == StepStatus.SUCCESS]

    @property
    def is_complete(self) -> bool:
        return all(s.status == StepStatus.SUCCESS for s in self.subtasks)


# ---------------------------------------------------------------------------
# Memory snapshot
# ---------------------------------------------------------------------------


class MemoryEntry(BaseModel):
    """A single item stored in short-term or long-term memory."""

    id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    memory_type: str = "short_term"  # short_term | long_term | episodic


# ---------------------------------------------------------------------------
# Top-level agent state
# ---------------------------------------------------------------------------


class AgentState(BaseModel):
    """
    Complete mutable state of the agent for one task run.

    Passed through every component in the reasoning loop and persisted
    to disk on each step for crash recovery.
    """

    # Identity
    run_id: str = Field(..., description="Unique ID for this task run")
    task: str = Field(..., description="The original user-provided task description")

    # Status
    status: AgentStatus = AgentStatus.IDLE
    current_step: int = Field(0, description="1-indexed step counter; 0 means not started")
    max_steps: int = Field(30, description="Hard limit on reasoning steps")

    # Plan
    plan: TaskPlan | None = None

    # Reasoning history (full transcript)
    steps: list[ReasoningStep] = Field(default_factory=list)

    # Final output
    final_answer: str | None = None
    error: str | None = None

    # Timing
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None

    # Accumulated token usage
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @property
    def is_done(self) -> bool:
        return self.status in {
            AgentStatus.COMPLETE,
            AgentStatus.FAILED,
            AgentStatus.MAX_STEPS_REACHED,
        }

    @property
    def steps_remaining(self) -> int:
        return max(0, self.max_steps - self.current_step)

    def add_step(self, step: ReasoningStep) -> None:
        self.steps.append(step)
        self.current_step = step.step_number
        self.total_input_tokens += step.input_tokens
        self.total_output_tokens += step.output_tokens

    def latest_step(self) -> ReasoningStep | None:
        return self.steps[-1] if self.steps else None

    def context_messages(self) -> list[dict[str, Any]]:
        """
        Build the message list to send to the LLM on the next step.
        Includes the full thought/action/observation history.
        """
        messages: list[dict[str, Any]] = []
        for step in self.steps:
            # Assistant turn: thought + tool calls
            content: list[dict[str, Any]] = []
            if step.thought:
                content.append({"type": "text", "text": step.thought})
            for tc in step.tool_calls:
                content.append({
                    "type": "tool_use",
                    "id": tc.tool_use_id or f"tu_{step.step_number}",
                    "name": tc.tool_name,
                    "input": tc.tool_input,
                })
            if content:
                messages.append({"role": "assistant", "content": content})

            # User turn: tool results
            if step.tool_results:
                result_content = [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_use_id or f"tu_{step.step_number}",
                        "content": r.error if r.error else r.output,
                        "is_error": r.error is not None,
                    }
                    for r in step.tool_results
                ]
                messages.append({"role": "user", "content": result_content})

        return messages
