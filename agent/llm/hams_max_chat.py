"""
HAMS-MAX Chat Mode — simple chat tanpa tools dan tanpa extended thinking.
Dipakai oleh /chat/stream endpoint.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from agent.llm.hams_max_base import HamsMaxBase, HAMS_MAX_BASE_URL
from agent.llm.base import LLMResponse
import httpx


class HamsMaxChatLLM(HamsMaxBase):
    """Mode chat biasa — kirim pesan, dapat respons, stream supported."""

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload  = self._build_payload(messages, system=system)
        raw_text = await self._call_api_with_fallback(payload)
        return LLMResponse(
            thought=raw_text,
            action_type="final_answer",
            tool_calls=[],
            final_answer=raw_text,
            raw=raw_text,
        )

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        payload = self._build_payload(messages, system=system)
        return await self._call_api(payload)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        # Streaming hanya untuk Groq, fallback ke generate untuk NVIDIA
        if self._provider != "groq":
            result = await self.generate(messages, system=system)
            yield result.final_answer or ""
            return

        payload = self._build_payload(messages, system=system)
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream(
                "POST",
                f"{HAMS_MAX_BASE_URL}/v1/chat/stream",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    if chunk:
                        yield chunk
