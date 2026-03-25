"""
Abstract base class for all LLM providers.

Every provider (Anthropic, OpenAI, Ollama) must implement:
  - generate()       â†’ full agentic response with tool call support
  - generate_text()  â†’ simple text-only completion (used by planner)
  - stream()         â†’ streaming text generation

This abstraction lets the agent switch providers without changing any
other code â€” just swap the LLM instance passed to Agent().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


# ---------------------------------------------------------------------------
# Response dataclass returned by all providers
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """
    Unified response from any LLM provider.

    The ReasoningLoop only ever sees this type â€” never provider-specific
    response objects.
    """

    # The model's reasoning / text output
    thought: str = ""

    # Action decision
    action_type: str = "tool_call"          # "tool_call" | "final_answer" | "ask_clarification"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str | None = None

    # Token accounting
    input_tokens: int = 0
    output_tokens: int = 0

    # Raw provider response (for debugging)
    raw: Any = None


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseLLM(ABC):
    """
    Abstract interface that every LLM provider must implement.

    Subclasses only need to translate between their provider's API format
    and the unified LLMResponse / message list format.
    """

    def __init__(
        self,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    # -----------------------------------------------------------------------
    # Required
    # -----------------------------------------------------------------------

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Main agentic generation call.

        Args:
            messages:  Conversation history in OpenAI-style format.
            tools:     Tool schemas in provider-native format.
            system:    System prompt string.

        Returns:
            LLMResponse with thought, tool_calls or final_answer, token counts.
        """
        ...

    @abstractmethod
    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        """
        Simple text-only generation â€” no tool calls.
        Used by TaskPlanner and other non-agentic components.
        """
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Yield text chunks as they arrive from the provider."""
        ...

    # -----------------------------------------------------------------------
    # Shared helpers
    # -----------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model!r})"
