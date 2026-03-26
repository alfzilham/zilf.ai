"""
LLM Router â€” selects and falls back between providers automatically.

Supports two modes:
  1. Single provider  â€” always use one LLM
  2. Fallback chain   â€” try providers in order; move to next on error

Usage::

    # Single provider
    router = LLMRouter.from_env()

    # Fallback: try Claude first, fall back to GPT-4o, then local Ollama
    router = LLMRouter(
        primary=GroqLLM(),
        fallbacks=[GoogleLLM(), OllamaLLM()],
    )

    response = await router.generate(messages=[...], tools=[...])
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from agent.llm.base import BaseLLM, LLMResponse


class LLMRouter(BaseLLM):
    """
    Routes LLM requests to the best available provider.

    On failure, automatically retries with exponential back-off,
    then tries the next fallback provider.
    """

    def __init__(
        self,
        primary: BaseLLM,
        fallbacks: list[BaseLLM] | None = None,
        max_retries: int = 3,
    ) -> None:
        # Use primary's settings for repr/logging
        super().__init__(
            model=primary.model,
            max_tokens=primary.max_tokens,
            temperature=primary.temperature,
        )
        self._primary = primary
        self._fallbacks = fallbacks or []
        self._max_retries = max_retries
        self._providers: list[BaseLLM] = [primary, *self._fallbacks]

    # -----------------------------------------------------------------------
    # Factory
    # -----------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "LLMRouter":
        """
        Build a router from environment variables.

        B4 FIX: Menggunakan if (bukan elif) untuk setiap provider,
        sehingga semua provider yang tersedia bisa masuk ke fallback chain.
        Provider yang dipilih via AGENT_LLM_PROVIDER jadi primary.
        """
        from agent.llm.ollama_provider import OllamaLLM

        provider_name = os.environ.get("AGENT_LLM_PROVIDER", "ollama").lower()
        model = os.environ.get("AGENT_MODEL")

        primary: BaseLLM | None = None
        fallbacks: list[BaseLLM] = []

        # â”€â”€ 1. Tentukan primary berdasarkan AGENT_LLM_PROVIDER â”€â”€
        if provider_name == "zilf-max":
            try:
                from agent.llm.zilf_max_provider import ZilfMaxLLM
                primary = ZilfMaxLLM(model=model or "groq")
            except (ImportError, RuntimeError) as e:
                logger.warning(f"[router] ZilfMaxLLM not available: {e}")

        elif provider_name == "groq":
            try:
                from agent.llm.groq_provider import GroqLLM
                primary = GroqLLM(model=model or "llama-3.3-70b-versatile")
            except ImportError:
                pass

        elif provider_name == "google":
            try:
                from agent.llm.google_provider import GoogleLLM
                primary = GoogleLLM(model=model or "gemini-1.5-flash")
            except ImportError:
                pass

        elif provider_name == "together":
            try:
                from agent.llm.together_provider import TogetherLLM
                primary = TogetherLLM(model=model or "Qwen/Qwen2.5-Coder-32B-Instruct")
            except ImportError:
                pass

        elif provider_name == "ollama":
            primary = OllamaLLM(model=model or "deepseek-coder")

        # â”€â”€ 2. Tambahkan semua provider lain sebagai fallback â”€â”€
        # B4 FIX: Setiap provider dicek INDEPENDEN (if, bukan elif)

        if os.environ.get("ZILF_MAX_API_KEY"):
            try:
                from agent.llm.zilf_max_provider import ZilfMaxLLM
                if primary is None or not isinstance(primary, ZilfMaxLLM):
                    fallbacks.append(ZilfMaxLLM(model="groq"))
            except (ImportError, RuntimeError):
                pass

        if os.environ.get("GROQ_API_KEY"):
            try:
                from agent.llm.groq_provider import GroqLLM
                if primary is None or not isinstance(primary, GroqLLM):
                    fallbacks.append(GroqLLM(model="llama-3.3-70b-versatile"))
            except ImportError:
                pass

        if os.environ.get("GOOGLE_API_KEY"):
            try:
                from agent.llm.google_provider import GoogleLLM
                if primary is None or not isinstance(primary, GoogleLLM):
                    fallbacks.append(GoogleLLM(model="gemini-1.5-flash"))
            except ImportError:
                pass

        # â”€â”€ 3. Ollama selalu jadi fallback terakhir â”€â”€
        has_ollama = isinstance(primary, OllamaLLM) or any(
            isinstance(f, OllamaLLM) for f in fallbacks
        )
        if not has_ollama:
            fallbacks.append(OllamaLLM(model="deepseek-coder"))

        # â”€â”€ 4. Jika tidak ada primary, ambil dari fallbacks â”€â”€
        if primary is None:
            if fallbacks:
                primary = fallbacks.pop(0)
            else:
                primary = OllamaLLM(model="deepseek-coder")

        logger.info(
            f"[router] Primary: {primary} | Fallbacks: {[str(f) for f in fallbacks]}"
        )
        return cls(primary=primary, fallbacks=fallbacks)

    # -----------------------------------------------------------------------
    # BaseLLM interface
    # -----------------------------------------------------------------------

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        return await self._call_with_fallback(
            "generate", messages=messages, tools=tools, system=system, **kwargs
        )

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        return await self._call_with_fallback(
            "generate_text", messages=messages, system=system, max_tokens=max_tokens, **kwargs
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        # Streaming always uses the primary provider only
        async for chunk in self._primary.stream(messages, system=system, **kwargs):
            yield chunk

    # -----------------------------------------------------------------------
    # Internal: fallback logic
    # -----------------------------------------------------------------------

    async def _call_with_fallback(self, method: str, **kwargs: Any) -> Any:
        last_exc: Exception | None = None

        for provider in self._providers:
            try:
                result = await self._call_with_retry(provider, method, **kwargs)
                return result
            except Exception as exc:
                logger.warning(
                    f"[router] {provider} failed ({exc}). "
                    f"{'Trying next fallback...' if provider != self._providers[-1] else 'No more fallbacks.'}"
                )
                last_exc = exc

        raise RuntimeError(
            f"All {len(self._providers)} LLM provider(s) failed. Last error: {last_exc}"
        ) from last_exc

    async def _call_with_retry(self, provider: BaseLLM, method: str, **kwargs: Any) -> Any:
        """Retry a single provider up to max_retries times with exponential back-off."""
        attempt = 0
        delay = 1.0

        while attempt < self._max_retries:
            try:
                return await getattr(provider, method)(**kwargs)
            except Exception as exc:
                attempt += 1
                if attempt >= self._max_retries:
                    raise
                import asyncio
                logger.debug(f"[router] Retry {attempt}/{self._max_retries} for {provider}: {exc}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)