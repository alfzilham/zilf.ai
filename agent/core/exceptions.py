"""
Custom exception hierarchy for the Zilf AI.

Every error carries:
  - message       : human-readable description
  - recoverable   : whether the agent can retry / recover automatically
  - context       : dict of structured metadata for logging & recovery decisions
  - timestamp     : UTC ISO-8601 string

Error categories:
  AgentError
  â”œâ”€â”€ LLMError
  â”‚   â”œâ”€â”€ RateLimitError
  â”‚   â”œâ”€â”€ LLMTimeoutError
  â”‚   â”œâ”€â”€ ContentPolicyError
  â”‚   â””â”€â”€ ContextLengthError
  â”œâ”€â”€ ToolError
  â”‚   â”œâ”€â”€ FileNotFoundToolError
  â”‚   â”œâ”€â”€ PermissionToolError
  â”‚   â”œâ”€â”€ SyntaxToolError
  â”‚   â”œâ”€â”€ SubprocessToolError
  â”‚   â””â”€â”€ ToolTimeoutError
  â”œâ”€â”€ LogicError
  â”‚   â”œâ”€â”€ InfiniteLoopError
  â”‚   â”œâ”€â”€ GoalDriftError
  â”‚   â””â”€â”€ HallucinatedPathError
  â””â”€â”€ EnvironmentError
      â”œâ”€â”€ DockerError
      â”œâ”€â”€ NetworkError
      â””â”€â”€ DiskFullError
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class AgentError(Exception):
    """Base class for all agent errors."""

    def __init__(
        self,
        message: str,
        *,
        recoverable: bool = True,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.recoverable = recoverable
        self.context: dict[str, Any] = context or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.__class__.__name__,
            "message": self.message,
            "recoverable": self.recoverable,
            "context": self.context,
            "timestamp": self.timestamp,
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.message!r}, recoverable={self.recoverable})"


# ---------------------------------------------------------------------------
# LLM API Errors
# ---------------------------------------------------------------------------


class LLMError(AgentError):
    """Base for all LLM provider errors."""


class RateLimitError(LLMError):
    """Provider rate limit hit â€” retry after backoff."""

    def __init__(self, retry_after: float = 60.0, **kwargs: Any) -> None:
        super().__init__(
            f"Rate limit exceeded; retry after {retry_after:.0f}s",
            recoverable=True,
            **kwargs,
        )
        self.retry_after = retry_after


class LLMTimeoutError(LLMError):
    """LLM generation timed out."""


class ContentPolicyError(LLMError):
    """Prompt or completion violated provider content policy."""

    def __init__(self, policy_code: str = "unknown", **kwargs: Any) -> None:
        super().__init__(
            f"Content policy violation: {policy_code}",
            recoverable=False,
            **kwargs,
        )
        self.policy_code = policy_code


class ContextLengthError(LLMError):
    """Input tokens exceeded model context window."""

    def __init__(self, token_count: int = 0, max_tokens: int = 0, **kwargs: Any) -> None:
        super().__init__(
            f"Context overflow: {token_count:,} tokens (max {max_tokens:,})",
            recoverable=True,
            **kwargs,
        )
        self.token_count = token_count
        self.max_tokens = max_tokens


# ---------------------------------------------------------------------------
# Tool Errors
# ---------------------------------------------------------------------------


class ToolError(AgentError):
    """Base for tool execution errors."""

    def __init__(self, tool_name: str, message: str, **kwargs: Any) -> None:
        super().__init__(f"[{tool_name}] {message}", **kwargs)
        self.tool_name = tool_name


class FileNotFoundToolError(ToolError):
    """Requested file does not exist."""


class PermissionToolError(ToolError):
    """Insufficient permissions for the filesystem operation."""


class SyntaxToolError(ToolError):
    """Agent-generated code contains a syntax error."""

    def __init__(self, tool_name: str, line: int, detail: str, **kwargs: Any) -> None:
        super().__init__(tool_name, f"Syntax error on line {line}: {detail}", **kwargs)
        self.line = line
        self.detail = detail


class SubprocessToolError(ToolError):
    """Shell command exited with non-zero status."""

    def __init__(
        self,
        tool_name: str,
        returncode: int,
        stderr: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            tool_name,
            f"Exit code {returncode}: {stderr[:200]}",
            recoverable=True,
            **kwargs,
        )
        self.returncode = returncode
        self.stderr = stderr


class ToolTimeoutError(ToolError):
    """Tool execution exceeded its configured timeout."""


# ---------------------------------------------------------------------------
# Logic Errors
# ---------------------------------------------------------------------------


class LogicError(AgentError):
    """Base for agent reasoning failures."""


class InfiniteLoopError(LogicError):
    """Agent detected a repeating tool-call pattern."""

    def __init__(self, loop_length: int, step: int, **kwargs: Any) -> None:
        super().__init__(
            f"Infinite loop detected at step {step} (cycle â‰ˆ {loop_length} steps)",
            recoverable=True,
            **kwargs,
        )
        self.loop_length = loop_length
        self.step = step


class GoalDriftError(LogicError):
    """Agent reasoning has diverged from the original task."""


class HallucinatedPathError(LogicError):
    """Agent referenced a non-existent file or function."""

    def __init__(self, path: str, **kwargs: Any) -> None:
        super().__init__(f"Hallucinated path: '{path}'", **kwargs)
        self.path = path


# ---------------------------------------------------------------------------
# Environment Errors
# ---------------------------------------------------------------------------


class AgentEnvironmentError(AgentError):
    """Base for infrastructure failures."""


class DockerError(AgentEnvironmentError):
    """Container crash or unexpected exit."""


class NetworkError(AgentEnvironmentError):
    """Network connectivity failure."""


class DiskFullError(AgentEnvironmentError):
    """Workspace storage exhausted â€” not recoverable automatically."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            "Disk quota exceeded â€” free up workspace space before retrying.",
            recoverable=False,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Convenience mapping: error class name â†’ category string
# ---------------------------------------------------------------------------

ERROR_CATEGORY: dict[str, str] = {
    "LLMError": "llm",
    "RateLimitError": "llm",
    "LLMTimeoutError": "llm",
    "ContentPolicyError": "llm",
    "ContextLengthError": "llm",
    "ToolError": "tool",
    "FileNotFoundToolError": "tool",
    "PermissionToolError": "tool",
    "SyntaxToolError": "tool",
    "SubprocessToolError": "tool",
    "ToolTimeoutError": "tool",
    "LogicError": "logic",
    "InfiniteLoopError": "logic",
    "GoalDriftError": "logic",
    "HallucinatedPathError": "logic",
    "AgentEnvironmentError": "environment",
    "DockerError": "environment",
    "NetworkError": "environment",
    "DiskFullError": "environment",
}


def error_category(exc: AgentError) -> str:
    return ERROR_CATEGORY.get(type(exc).__name__, "unknown")
