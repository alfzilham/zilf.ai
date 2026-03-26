"""
GLM (Zhipu AI) LLM Provider
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx
from loguru import logger

from agent.llm.base import BaseLLM, LLMResponse
from agent.config.settings import get_settings


class GLMLLM(BaseLLM):
    """
    Integration with Zhipu AI's GLM models (OpenAI-compatible API).
    """

    BASE_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    DEFAULT_MODEL = "glm-4-plus"

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
        settings = get_settings()
        # Fallback sequence: parameter -> os.environ -> settings.py
        self.api_key = api_key or os.environ.get("GLM_API_KEY") or settings.get_api_key("glm")
        if not self.api_key:
            raise ValueError("GLM_API_KEY environment variable is required")

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Agentic generation with tool call support.
        """
        headers = self._get_headers()

        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        
        for msg in messages:
            api_messages.append(msg)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        logger.debug(f"[GLMLLM] Generating with {self.model}")

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(self.BASE_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]
        message = choice.get("message", {})
        
        content = message.get("content") or ""
        tool_calls_raw = message.get("tool_calls")

        # Parse tool calls if present
        if tool_calls_raw:
            return LLMResponse(
                thought="",
                action_type="tool_call",
                tool_calls=tool_calls_raw,
                final_answer=None,
                raw=data
            )

        # Fallback to plain text
        return LLMResponse(
            thought="",
            action_type="final_answer",
            final_answer=content,
            raw=data
        )

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        """Simple text generation."""
        headers = self._get_headers()

        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(self.BASE_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"] or ""

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Streaming text generation."""
        headers = self._get_headers()

        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", self.BASE_URL, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip() or line.strip() == "data: [DONE]":
                        continue
                    
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except Exception:
                            continue
