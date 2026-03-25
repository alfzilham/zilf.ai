"""
Groq provider â€” ultra-fast cloud inference via Groq API.

Supports models:
  - llama3-70b-8192
  - llama3-8b-8192
  - mixtral-8x7b-32768
  - gemma2-9b-it

Requires: GROQ_API_KEY in .env
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncIterator

from loguru import logger

from agent.llm.base import BaseLLM, LLMResponse
from agent.core.state import ActionType, ToolCall


class GroqLLM(BaseLLM):
    """
    Groq cloud LLM provider.

    Tool calling is implemented via JSON-mode prompting.

    Usage::

        llm = GroqLLM(model="llama3-70b-8192")
        response = await llm.generate(messages=[...], tools=[...], system="...")
    """

    DEFAULT_MODEL = "llama-3.3-70b-versatile"
    TOOL_PROMPT_SUFFIX = """

Respond with a JSON object in one of these two formats:

To call a tool:
{"action": "tool_call", "tool": "<tool_name>", "input": {<tool_arguments>}, "thought": "<your reasoning>"}

When the task is complete:
{"action": "final_answer", "answer": "<your final response>", "thought": "<your reasoning>"}

Respond with ONLY the JSON â€” no markdown, no explanation outside the JSON.
"""

    def __init__(
        self,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        api_key: str | None = None,
    ) -> None:
        super().__init__(
            model=model or self.DEFAULT_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from groq import AsyncGroq  # type: ignore[import]
                self._client = AsyncGroq(api_key=self.api_key)
            except ImportError as exc:
                raise ImportError(
                    "groq package not installed. Run: pip install groq"
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

        full_system = self._build_system(system, tools)
        api_messages = self._flatten_messages(messages, full_system)

        logger.debug(f"[groq] Calling {self.model} â€” {len(api_messages)} messages")

        resp = await client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
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

        resp = await client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            max_tokens=max_tokens,
            temperature=self.temperature,
        )
        return resp.choices[0].message.content or ""

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

        stream = await client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=True,
        )
        async for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _build_system(self, system: str | None, tools: list[dict] | None) -> str:
        parts = [system or "You are a helpful AI coding assistant."]
        if tools:
            tool_descs = "\n".join(
                f"- {t['name']}: {t.get('description', '')}"
                for t in tools
            )
            parts.append(f"\n## Available Tools\n{tool_descs}")
        parts.append(self.TOOL_PROMPT_SUFFIX)
        return "\n".join(parts)

    def _flatten_messages(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
    ) -> list[dict[str, Any]]:
        result = []
        if system:
            result.append({"role": "system", "content": system})
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
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
        raw_text: str = resp.choices[0].message.content or ""
        raw_text = raw_text.strip()

        input_tokens = getattr(resp.usage, "prompt_tokens", 0)
        output_tokens = getattr(resp.usage, "completion_tokens", 0)

        # Strip markdown code fences
        raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning("[groq] Could not parse JSON response, treating as final answer.")
            return LLMResponse(
                thought=raw_text,
                action_type=ActionType.FINAL_ANSWER,
                final_answer=raw_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        action = data.get("action", "final_answer")
        thought = data.get("thought", "")

        if action == "tool_call":
            tc = ToolCall(
                tool_name=data.get("tool", ""),
                tool_input=data.get("input", {}),
            )
            return LLMResponse(
                thought=thought,
                action_type=ActionType.TOOL_CALL,
                tool_calls=[tc],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        else:
            return LLMResponse(
                thought=thought,
                action_type=ActionType.FINAL_ANSWER,
                final_answer=data.get("answer", raw_text),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
