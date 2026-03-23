"""
Google Gemini provider — cloud inference via Google AI Studio.

Supports models:
  - gemini-2.5-flash-lite   (tercepat, paling hemat)
  - gemini-2.5-flash        (seimbang)
  - gemini-2.5-pro          (paling capable)
  - gemini-2.0-flash        (stable)
  - gemini-1.5-flash        (legacy)
  - gemini-1.5-pro          (legacy)

Requires: GOOGLE_API_KEY in .env
SDK: pip install google-genai  ← SDK baru, bukan google-generativeai
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncIterator

from loguru import logger

from agent.llm.base import BaseLLM, LLMResponse
from agent.core.state import ActionType, ToolCall

# ── Model name mapping (frontend → Google API model ID) ───────────────────
_MODEL_ALIASES: dict[str, str] = {
    # Gemini 2.5
    "gemini-2.5-flash-lite":               "gemini-2.5-flash-lite",
    "gemini-2.5-flash-lite-preview-06-17": "gemini-2.5-flash-lite",
    "gemini-2.5-flash":                    "gemini-2.5-flash",
    "gemini-2.5-flash-preview-05-20":      "gemini-2.5-flash",
    "gemini-2.5-pro":                      "gemini-2.5-pro",
    "gemini-2.5-pro-preview-05-06":        "gemini-2.5-pro",
    "gemini-2.5-pro-preview-06-05":        "gemini-2.5-pro",   # ← fix tanggal
    # Gemini 2.0
    "gemini-2.0-flash":                    "gemini-2.0-flash",
    "gemini-2.0-flash-exp":                "gemini-2.0-flash",
    # Gemini 1.5 (legacy)
    "gemini-1.5-flash":                    "gemini-1.5-flash",
    "gemini-1.5-pro":                      "gemini-1.5-pro",
}


def _resolve_model(model: str) -> str:
    """Normalize model name ke ID yang valid di Google API."""
    return _MODEL_ALIASES.get(model, model)


class GoogleLLM(BaseLLM):
    """
    Google Gemini cloud LLM provider.
    Menggunakan SDK baru: google-genai (pip install google-genai)

    Tool calling diimplementasikan via JSON-mode prompting.

    Usage::
        llm = GoogleLLM(model="gemini-2.5-flash")
        response = await llm.generate(messages=[...], tools=[...], system="...")
    """

    DEFAULT_MODEL = "gemini-2.5-flash"

    TOOL_PROMPT_SUFFIX = """

Respond with a JSON object in one of these two formats:

To call a tool:
{"action": "tool_call", "tool": "<tool_name>", "input": {<tool_arguments>}, "thought": "<your reasoning>"}

When the task is complete:
{"action": "final_answer", "answer": "<your final response>", "thought": "<your reasoning>"}

Respond with ONLY the JSON — no markdown, no explanation outside the JSON.
"""

    def __init__(
        self,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        api_key: str | None = None,
    ) -> None:
        resolved = _resolve_model(model or self.DEFAULT_MODEL)
        super().__init__(
            model=resolved,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._client: Any = None

        logger.info(f"[google] model={self.model} (requested={model})")

    def _get_client(self) -> Any:
        """Lazy-init google-genai client."""
        if self._client is None:
            try:
                from google import genai  # type: ignore[import]
                self._client = genai.Client(api_key=self.api_key)
            except ImportError as exc:
                raise ImportError(
                    "google-genai package not installed. Run: pip install google-genai"
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
        prompt      = self._build_prompt(messages, full_system)

        logger.debug(f"[google] Calling {self.model} — {len(messages)} messages")

        from google.genai import types  # type: ignore[import]

        response = await client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=self.max_tokens,
                temperature=self.temperature,
            ),
        )

        return self._parse_response(response)

    # -----------------------------------------------------------------------
    # generate_text()
    # -----------------------------------------------------------------------

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        client  = self._get_client()
        prompt  = self._build_prompt(messages, system)

        from google.genai import types  # type: ignore[import]

        response = await client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=self.temperature,
            ),
        )
        return response.text or ""

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
        prompt = self._build_prompt(messages, system)

        from google.genai import types  # type: ignore[import]

        async for chunk in await client.aio.models.generate_content_stream(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=self.max_tokens,
                temperature=self.temperature,
            ),
        ):
            if chunk.text:
                yield chunk.text

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _build_system(self, system: str | None, tools: list[dict] | None) -> str:
        parts = [system or "You are a helpful AI assistant."]
        if tools:
            tool_descs = "\n".join(
                f"- {t['name']}: {t.get('description', '')}"
                for t in tools
            )
            parts.append(f"\n## Available Tools\n{tool_descs}")
        parts.append(self.TOOL_PROMPT_SUFFIX)
        return "\n".join(parts)

    def _build_prompt(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
    ) -> str:
        """Flatten conversation history ke single prompt string untuk Gemini."""
        parts = []
        if system:
            parts.append(f"[System]\n{system}\n")
        for m in messages:
            role    = m.get("role", "user").capitalize()
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_result":
                            text_parts.append(f"[Tool result] {block.get('content', '')}")
                        elif block.get("type") == "tool_use":
                            text_parts.append(
                                f"[Tool call: {block['name']}] {json.dumps(block.get('input', {}))}"
                            )
                content = "\n".join(text_parts)
            parts.append(f"[{role}]\n{content}")
        return "\n\n".join(parts)

    def _parse_response(self, resp: Any) -> LLMResponse:
        raw_text: str = resp.text or ""
        raw_text = raw_text.strip()

        # Strip markdown code fences
        raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning("[google] Could not parse JSON response, treating as final answer.")
            return LLMResponse(
                thought=raw_text,
                action_type=ActionType.FINAL_ANSWER,
                final_answer=raw_text,
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
            )
        else:
            return LLMResponse(
                thought=thought,
                action_type=ActionType.FINAL_ANSWER,
                final_answer=data.get("answer", raw_text),
            )