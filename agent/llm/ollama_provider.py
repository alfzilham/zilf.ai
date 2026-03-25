"""
Ollama provider â€” run LLMs locally with zero API cost. (v2)

Supports any model available via `ollama pull`:
  - llama3, llama3:70b
  - codestral, qwen2.5-coder
  - mistral, mixtral
  - deepseek-coder, phi3

Requires: Ollama running locally (`ollama serve`) â€” no API key needed.
Default base URL: http://localhost:11434
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncIterator

from loguru import logger

from agent.llm.base import BaseLLM, LLMResponse
from agent.core.state import ActionType, ToolCall


class OllamaLLM(BaseLLM):
    """
    Ollama local LLM provider.

    Tool calling is implemented via JSON-mode prompting because most
    Ollama models don't natively support function calling. The agent
    prompts the model to output a JSON action block, which we parse.

    Usage::

        llm = OllamaLLM(model="llama3")
        response = await llm.generate(messages=[...], tools=[...], system="...")
    """

    DEFAULT_MODEL = "llama3"

    TOOL_PROMPT_SUFFIX = """

## MANDATORY RESPONSE FORMAT

You MUST respond with a single JSON object. No prose. No markdown. No explanation outside JSON.

### If you need to use a tool (CREATE file, RUN command, READ file, SEARCH web):
{"action": "tool_call", "tool": "<tool_name>", "input": {<tool_arguments>}, "thought": "<your reasoning>"}

### If the task is 100% complete and verified:
{"action": "final_answer", "answer": "<your complete response>", "thought": "<your reasoning>"}

## CRITICAL RULES:
1. For ANY task that involves creating a file â†’ use write_file tool FIRST
2. For ANY task that involves running code â†’ use run_command tool FIRST  
3. NEVER use final_answer if you haven't called write_file yet for file creation tasks
4. After write_file â†’ use run_command to verify â†’ THEN use final_answer

## Tool Reference:
- write_file: {"action": "tool_call", "tool": "write_file", "input": {"path": "/workspace/file.py", "content": "..."}, "thought": "..."}
- run_command: {"action": "tool_call", "tool": "run_command", "input": {"command": "python /workspace/file.py"}, "thought": "..."}
- read_file: {"action": "tool_call", "tool": "read_file", "input": {"path": "/workspace/file.py"}, "thought": "..."}
- list_dir: {"action": "tool_call", "tool": "list_dir", "input": {"directory": "/workspace"}, "thought": "..."}
- web_search: {"action": "tool_call", "tool": "web_search", "input": {"query": "..."}, "thought": "..."}

START YOUR RESPONSE WITH { AND END WITH }
"""

    def __init__(
        self,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            model=model or self.DEFAULT_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import ollama  # type: ignore[import]
                self._client = ollama.AsyncClient(host=self.base_url)
            except ImportError as exc:
                raise ImportError(
                    "ollama package not installed. Run: pip install ollama"
                ) from exc
        return self._client

    # -----------------------------------------------------------------------
    # generate()
    # -----------------------------------------------------------------------

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._get_client()

        # Build system prompt with tool descriptions + JSON format instructions
        full_system = self._build_system(system, tools)
        api_messages = self._flatten_messages(messages, full_system)

        logger.debug(f"[ollama] Calling {self.model} â€” {len(api_messages)} messages")

        resp = await client.chat(
            model=self.model,
            messages=api_messages,
            options={"temperature": self.temperature, "num_predict": self.max_tokens},
        )

        return self._parse_response(resp)

    # -----------------------------------------------------------------------
    # generate_text()
    # -----------------------------------------------------------------------

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        client = self._get_client()
        api_messages = self._flatten_messages(messages, system)

        resp = await client.chat(
            model=self.model,
            messages=api_messages,
            options={"temperature": self.temperature, "num_predict": max_tokens},
        )
        return resp["message"]["content"] if isinstance(resp, dict) else resp.message.content

    # -----------------------------------------------------------------------
    # stream()
    # -----------------------------------------------------------------------

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        client = self._get_client()
        api_messages = self._flatten_messages(messages, system)

        async for chunk in await client.chat(
            model=self.model,
            messages=api_messages,
            stream=True,
        ):
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield content

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _build_system(self, system: str | None, tools: list[dict] | None) -> str:
        parts = [system or "You are a helpful AI coding assistant."]
        if tools:
            tool_names = ", ".join(t['name'] for t in tools)
            parts.append(f"\n## Available Tools: {tool_names}")
        parts.append(self.TOOL_PROMPT_SUFFIX)
        return "\n".join(parts)

    def _flatten_messages(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
    ) -> list[dict[str, Any]]:
        """Prepend system as a 'system' role message (Ollama supports this)."""
        result = []
        if system:
            result.append({"role": "system", "content": system})
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            # Flatten list-type content (tool results etc.) to plain text
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block["text"])
                        elif block.get("type") == "tool_result":
                            parts.append(f"[Tool result] {block.get('content', '')}")
                        elif block.get("type") == "tool_use":
                            parts.append(
                                f"[Tool call: {block['name']}] {json.dumps(block.get('input', {}))}"
                            )
                content = "\n".join(parts)
            result.append({"role": role, "content": content})
        return result

    def _parse_response(self, resp: Any) -> LLMResponse:
        """Parse the JSON action block from the model's text response."""
        raw_text: str = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
        raw_text = raw_text.strip()

        # Strip markdown code fences
        raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)

        # Try to extract JSON object â€” find first { to last }
        start = raw_text.find("{")
        end = raw_text.rfind("}") + 1

        if start != -1 and end > start:
            json_str = raw_text[start:end]
            try:
                data = json.loads(json_str)
                action = data.get("action", "final_answer")
                thought = data.get("thought", "")

                if action == "tool_call":
                    tool_name = data.get("tool", "")
                    tool_input = data.get("input", {})

                    # Validate tool call has required fields
                    if tool_name:
                        tc = ToolCall(
                            tool_name=tool_name,
                            tool_input=tool_input,
                        )
                        logger.debug(f"[ollama] Parsed tool_call: {tool_name}({list(tool_input.keys())})")
                        return LLMResponse(
                            thought=thought,
                            action_type=ActionType.TOOL_CALL,
                            tool_calls=[tc],
                        )

                # final_answer
                answer = data.get("answer", "")
                if not answer:
                    answer = raw_text
                return LLMResponse(
                    thought=thought,
                    action_type=ActionType.FINAL_ANSWER,
                    final_answer=answer,
                )

            except json.JSONDecodeError:
                pass

        # Model didn't follow JSON format â€” treat whole text as final answer
        logger.warning("[ollama] Could not parse JSON response, treating as final answer.")
        return LLMResponse(
            thought=raw_text,
            action_type=ActionType.FINAL_ANSWER,
            final_answer=raw_text,
        )