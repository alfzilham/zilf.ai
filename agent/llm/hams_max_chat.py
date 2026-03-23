"""
HAMS-MAX Chat Mode — simple chat tanpa tools dan tanpa extended thinking.
Dipakai oleh /chat/stream endpoint.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from agent.llm.hams_max_base import HamsMaxBase
from agent.llm.base import LLMResponse


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
        return await self._call_api_with_fallback(payload)  # fix: was _call_api

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        # Selalu pakai generate() + fallback — NVIDIA tidak support streaming endpoint
        # dan setelah fallback _provider bisa berubah sehingga cek groq tidak reliable
        result = await self.generate(messages, system=system)
        yield result.final_answer or ""