"""
ZILF-MAX Provider â€” router utama yang memilih mode yang tepat.

Mode:
  - ZilfMaxChatLLM     â†’ chat biasa (default)
  - ZilfMaxAgentLLM    â†’ agent/ReAct dengan tools
  - ZilfMaxThinkingLLM â†’ chat dengan extended thinking

Backward compatible: ZilfMaxLLM masih bisa dipakai seperti biasa.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from agent.llm.zilf_max_base import ZilfMaxBase, resolve_model
from agent.llm.zilf_max_chat import ZilfMaxChatLLM
from agent.llm.zilf_max_agent import ZilfMaxAgentLLM
from agent.llm.zilf_max_thinking import ZilfMaxThinkingLLM
from agent.llm.base import LLMResponse


class ZilfMaxLLM(ZilfMaxBase):
    """
    Router utama â€” delegate ke mode yang tepat berdasarkan
    apakah ada tools (agent) atau extended=True (thinking).

    Usage (tidak berubah dari sebelumnya):
        llm = ZilfMaxLLM(model="groq", extended=True)
        response = await llm.generate(messages, tools=tools)
    """

    def __init__(
        self,
        model: str = "groq",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        extended: bool = False,
    ) -> None:
        super().__init__(model=model, max_tokens=max_tokens, temperature=temperature)
        self._extended = extended

        # Inisialisasi semua mode dengan model yang sama
        self._chat    = ZilfMaxChatLLM(model=model, max_tokens=max_tokens, temperature=temperature)
        self._agent   = ZilfMaxAgentLLM(model=model, max_tokens=max_tokens, temperature=temperature)
        self._thinking = ZilfMaxThinkingLLM(model=model, max_tokens=max_tokens, temperature=temperature)

    def _pick(self, has_tools: bool) -> ZilfMaxBase:
        """Pilih mode yang tepat."""
        if has_tools:
            return self._agent          # Agent: ReAct, tanpa extended thinking
        if self._extended:
            return self._thinking       # Chat + Extended Thinking
        return self._chat               # Chat biasa

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        extended: bool | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        use_extended = extended if extended is not None else self._extended
        # Override _extended sementara kalau dipassing dari luar
        if extended is not None:
            self._extended = extended
        mode = self._pick(has_tools=bool(tools))
        self._extended = use_extended  # restore
        return await mode.generate(messages, tools=tools, system=system, **kwargs)

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        extended: bool = False,
        **kwargs: Any,
    ) -> str:
        if extended or self._extended:
            return await self._thinking.generate_text(messages, system=system, max_tokens=max_tokens)
        return await self._chat.generate_text(messages, system=system, max_tokens=max_tokens)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        mode = self._pick(has_tools=False)
        async for chunk in mode.stream(messages, system=system, **kwargs):
            yield chunk
