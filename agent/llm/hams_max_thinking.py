"""
HAMS-MAX Thinking Mode — chat dengan Extended Thinking (<think> blocks).
HANYA untuk chat mode (tanpa tools). Tidak dipakai di agent mode.
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

from loguru import logger

from agent.llm.hams_max_base import HamsMaxBase, HAMS_MAX_BASE_URL
from agent.llm.base import LLMResponse
import httpx

_THINKING_PROMPT = """Before answering, think deeply inside <think>...</think> tags.
Use this space to break down the problem, consider approaches, and plan your response.
After </think>, provide your actual answer.

"""


def _extract_thinking(text: str) -> tuple[str, str]:
    """Pisahkan <think>...</think> dari teks. Return (thinking, answer)."""
    think_blocks = re.findall(r'<think>(.*?)</think>', text, re.DOTALL | re.IGNORECASE)
    answer = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    thinking = "\n\n".join(block.strip() for block in think_blocks)
    return thinking, answer


class HamsMaxThinkingLLM(HamsMaxBase):
    """
    Mode extended thinking — chat biasa tapi model berpikir keras
    di dalam <think> tags sebelum menjawab.
    Tidak cocok untuk agent mode.
    """

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        full_system = _THINKING_PROMPT + (system or "")
        payload  = self._build_payload(messages, system=full_system)
        raw_text = await self._call_api_with_fallback(payload)

        thinking, answer = _extract_thinking(raw_text)
        return LLMResponse(
            thought=thinking or raw_text,
            action_type="final_answer",
            tool_calls=[],
            final_answer=answer or raw_text,
            raw=raw_text,
        )

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        """Return JSON string {"thinking": "...", "answer": "..."} untuk api.py."""
        full_system = _THINKING_PROMPT + (system or "")
        payload  = self._build_payload(messages, system=full_system)
        raw_text = await self._call_api_with_fallback(payload)

        thinking, answer = _extract_thinking(raw_text)
        return json.dumps({
            "thinking": thinking,
            "answer":   answer,
            "raw":      raw_text,
        }, ensure_ascii=False)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        # Stream answer saja (tanpa thinking block)
        if self._provider != "groq":
            result = await self.generate(messages, system=system)
            yield result.final_answer or ""
            return

        full_system = _THINKING_PROMPT + (system or "")
        payload = self._build_payload(messages, system=full_system)
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
