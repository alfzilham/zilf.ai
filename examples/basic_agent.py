"""
Basic Agent Script — complete end-to-end demonstration.

This script shows how every chapter connects:
  Ch00 Overview      → Agent goal and capabilities
  Ch01 Architecture  → Agent + ReasoningLoop + state
  Ch02 Tech Stack    → AnthropicLLM (or MockLLM if no key)
  Ch03 Environment   → Settings loads .env
  Ch04 Sandboxing    → DockerSandbox (optional)
  Ch05 Tools         → ToolRegistry with all tools
  Ch06 Workflow      → ReAct loop, error handling, prompts
  Ch07 This file     → Wires it all together

Run (with real API key):
    python examples/basic_agent.py

Run (demo mode — no API key needed):
    python examples/basic_agent.py --demo

Run (custom task):
    python examples/basic_agent.py "Write a Python function to check if a number is prime"
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path when running directly
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


# ---------------------------------------------------------------------------
# MockLLM — for demo/testing without a real API key
# ---------------------------------------------------------------------------


class MockLLM:
    """
    Simulated LLM that replays a fixed script of responses.

    Useful for:
      - Testing the agent loop without API costs
      - CI/CD environments without API keys
      - Demonstrating the ReAct format

    Replace with AnthropicLLM / OpenAILLM for real tasks.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = responses or self._default_responses()
        self._index = 0

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Return the next scripted response as a fake LLMResponse."""
        from agent.llm.base import LLMResponse
        from agent.core.state import ActionType, ToolCall

        text = self._next()

        # Detect final answer
        if "TASK COMPLETE" in text or "final answer" in text.lower():
            return LLMResponse(
                thought=text,
                action_type=ActionType.FINAL_ANSWER,
                final_answer=text,
                input_tokens=100,
                output_tokens=50,
            )

        # Detect tool call (look for JSON block)
        import json, re
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
                        input_tokens=100,
                        output_tokens=60,
                    )
            except json.JSONDecodeError:
                pass

        return LLMResponse(
            thought=text,
            action_type=ActionType.FINAL_ANSWER,
            final_answer=text,
            input_tokens=100,
            output_tokens=50,
        )

    async def generate_text(
        self, messages: list[dict], system: str | None = None, max_tokens: int = 1024, **kwargs: Any
    ) -> str:
        return self._next()

    async def stream(self, messages: list[dict], system: str | None = None, **kwargs: Any):  # type: ignore
        for char in self._next():
            yield char

    def _next(self) -> str:
        if self._index < len(self._responses):
            resp = self._responses[self._index]
            self._index += 1
            return resp
        return 'TASK COMPLETE\nStatus: success\nSummary: Demo task finished.\nFiles changed: none\nTests: not applicable\nNotes: None'

    @staticmethod
    def _default_responses() -> list[str]:
        return [
            # Step 1: list workspace
            'I need to see what\'s in the workspace first.\n{"tool": "list_dir", "directory": "/workspace"}',
            # Step 2: write a hello world file
            'Let me create a simple Python script.\n{"tool": "write_file", "path": "/workspace/hello.py", "content": "print(\'Hello from AI Coding Agent!\')\\n"}',
            # Step 3: run it
            '{"tool": "run_command", "command": "python /workspace/hello.py"}',
            # Step 4: done
            "TASK COMPLETE\nStatus: success\nSummary: Created and ran hello.py successfully.\nFiles changed: /workspace/hello.py\nTests: not applicable\nNotes: None",
        ]


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def build_agent(use_mock: bool = False) -> Any:
    """
    Priority:
      1. use_mock=True      → MockLLM
      2. AGENT_LLM_PROVIDER=ollama → OllamaLLM  (default)
      3. GROQ_API_KEY set   → GroqLLM
      4. GOOGLE_API_KEY set → GoogleLLM
      5. Fallback           → MockLLM with warning
    """
    from agent.core.agent import Agent
    from agent.tools.registry import ToolRegistry

    registry = ToolRegistry.default()

    if use_mock:
        console.print("[dim]Using MockLLM (demo mode)[/dim]")
        llm = MockLLM()
        return Agent(llm=llm, tool_registry=registry, use_planner=False, verbose=True)

    from agent.llm.router import LLMRouter
    try:
        llm = LLMRouter.from_env()
        return Agent(llm=llm, tool_registry=registry, use_planner=True, verbose=True)
    except RuntimeError as e:
        console.print(f"[yellow]⚠ LLM init failed ({e}). Falling back to MockLLM.[/yellow]")
        return Agent(llm=MockLLM(), tool_registry=registry, use_planner=False, verbose=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_task(task: str, demo: bool = False) -> None:
    agent = build_agent(use_mock=demo)

    console.print(Panel(
        Text(task, style="bold white"),
        title="[cyan]AI Coding Agent[/cyan]",
        border_style="cyan",
    ))

    response = await agent.run(task)

    if response.success:
        console.print(Panel(
            response.final_answer or "(no output)",
            title="[green]✅ Complete[/green]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            response.error or "Unknown error",
            title=f"[red]❌ {response.status.value}[/red]",
            border_style="red",
        ))

    console.print(
        f"\n[dim]Steps: {response.steps_taken}  |  "
        f"Tokens: {response.total_input_tokens + response.total_output_tokens:,}  |  "
        f"Run ID: {response.run_id}[/dim]"
    )


def main() -> None:
    args = sys.argv[1:]
    demo = "--demo" in args
    args = [a for a in args if a != "--demo"]

    task = args[0] if args else (
        "Create a Python file called hello.py that prints 'Hello from AI Coding Agent!' "
        "and verify it runs correctly."
    )

    asyncio.run(run_task(task, demo=demo))


if __name__ == "__main__":
    main()
