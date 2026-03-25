"""
BaseTool â€” abstract interface that every tool must implement.

Tools are the agent's hands: they let it read files, run commands,
search the web, and execute code. Every tool exposes:
  - a name  (used by the LLM to call it)
  - a description  (tells the LLM *when* to use it â€” most important field)
  - an input_schema  (JSON Schema for arguments)
  - an execute() method  (the actual implementation)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """
    Abstract base for all agent tools.

    Subclass this and implement `execute()` to create a new tool.
    Register it with ToolRegistry to make it available to the agent.
    """

    # Override these in every subclass
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """
        Execute the tool and return a string result.

        Always returns a string â€” the LLM only understands text.
        Raise an exception on failure; the registry will catch it and
        return a formatted error string to the LLM.
        """
        ...

    # -----------------------------------------------------------------------
    # Schema export helpers (used by ToolRegistry)
    # -----------------------------------------------------------------------

    def to_anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def __repr__(self) -> str:
        return f"Tool({self.name!r})"
