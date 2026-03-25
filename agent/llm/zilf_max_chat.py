"""
ZILF-MAX Chat Mode â€” simple chat tanpa tools dan tanpa extended thinking.

Fixes applied:
  B18  â€” Token tracking via track_tokens=True
  B10  â€” True streaming via /v1/chat/stream endpoint
  A2   â€” generate_text() uses _build_payload() properly (consistent with B20)
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import httpx
from loguru import logger

from agent.llm.zilf_max_base import ZilfMaxBase, ZILF_MAX_BASE_URL
from agent.llm.base import LLMResponse


class ZilfMaxChatLLM(ZilfMaxBase):
    """Mode chat biasa â€” with token tracking and true streaming."""

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload = self._build_payload(messages, system=system)
        # B18 FIX: gunakan track_tokens=True untuk dapat token count
        raw_text, input_tokens, output_tokens = await self._call_api_with_fallback(
            payload, track_tokens=True
        )
        return LLMResponse(
            thought=raw_text,
            action_type="final_answer",
            tool_calls=[],
            final_answer=raw_text,
            raw=raw_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        """
        A2 FIX: Uses _build_payload() properly so system prompt
        is sent as separate system message (consistent with B20 fix).
        Extra kwargs (like extended=) are safely ignored.
        """
        payload = self._build_payload(messages, system=system)
        return await self._call_api_with_fallback(payload)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """
        B10 FIX: True streaming via zilf-max-api /v1/chat/stream endpoint.

        Sends SSE request and yields chunks as they arrive,
        instead of waiting for full response then yielding once.

        Falls back to non-streaming generate() if stream endpoint fails.
        """
        payload = self._build_payload(messages, system=system)

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    f"{ZILF_MAX_BASE_URL}/v1/chat/stream",
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        logger.warning(
                            f"[zilf-max-chat] stream endpoint returned {response.status_code}, "
                            f"falling back to non-streaming"
                        )
                        result = await self.generate(messages, system=system)
                        yield result.final_answer or ""
                        return

                    buffer = ""
                    async for chunk in response.aiter_text():
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            yield line + "\n"
                    if buffer:
                        yield buffer

        except httpx.HTTPError as e:
            logger.warning(
                f"[zilf-max-chat] stream failed ({e}), falling back to non-streaming"
            )
            result = await self.generate(messages, system=system)
            yield result.final_answer or ""

        except Exception as e:
            logger.error(f"[zilf-max-chat] unexpected stream error: {e}")
            result = await self.generate(messages, system=system)
            yield result.final_answer or ""