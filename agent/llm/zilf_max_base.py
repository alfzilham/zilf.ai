"""
ZILF-MAX Base â€” shared constants, helpers, dan base class.
Di-import oleh zilf_max_chat.py, zilf_max_agent.py, zilf_max_thinking.py.

Fixes applied:
  B18 â€” Token tracking via _call_api_tracked()
  B20 â€” System prompt sebagai role "system" terpisah di _build_payload()
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

import httpx
from loguru import logger

from agent.llm.base import BaseLLM, LLMResponse

ZILF_MAX_BASE_URL = "https://zilf-max-api-production.up.railway.app"

ZILF_MAX_MODELS: dict[str, str] = {
    "groq":       "llama-3.3-70b-versatile",
    "qwen":       "qwen3.5-122b-a10b",
    "deepseek":   "deepseek-v3.2",
    "nemotron":   "nemotron-3-super-120b-a12b",
    "kimi-think": "kimi-k2-instruct",
    "mistral":    "mistral-small-4-119b",
    "qwen397b":   "qwen3.5-397b-a17b",
    "kimi":       "kimi-k2.5",
    "minimax":    "minimax-m2.5",
    "glm":        "glm5",
    "step":       "step-3.5-flash",
}

_GROQ_MODELS: set[str] = {
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama-3.1-70b-versatile",
    "gemma2-9b-it",
    "gemma-7b-it",
    "mixtral-8x7b-32768",
    "compound-beta",
    "compound-beta-mini",
}

_FRONTEND_TO_ZILFMAX: dict[str, tuple[str, str]] = {
    "llama-3.3-70b-versatile": ("llama-3.3-70b-versatile", "groq"),
    "llama-3.1-8b-instant":    ("llama-3.1-8b-instant",    "groq"),
    "llama3-8b-8192":          ("llama-3.1-8b-instant",    "groq"),
    "llama3-70b-8192":         ("llama-3.3-70b-versatile", "groq"),
    "gemma2-9b-it":            ("gemma2-9b-it",             "groq"),
    "compound-beta":           ("compound-beta",            "groq"),
    "nvidia/qwen-3.5":         ("qwen",       "nvidia"),
    "nvidia/glm-5":            ("glm",        "nvidia"),
    "glm-4-flash":             ("glm",        "nvidia"),
    "glm-4-flashx":            ("glm",        "nvidia"),
    "glm-4-plus":              ("glm",        "nvidia"),
    "glm-z1-flash":            ("glm",        "nvidia"),
    "nvidia/minimax-m25":      ("minimax",    "nvidia"),
    "nvidia/kimi-k2.5":        ("kimi",       "nvidia"),
    "nvidia/stepfun-step3.5":  ("step",       "nvidia"),
    "nvidia/mistral-small-4":  ("mistral",    "nvidia"),
    "nvidia/qwen-397b":        ("qwen397b",   "nvidia"),
    "nvidia/deepseek-v3.2":    ("deepseek",   "nvidia"),
    "nvidia/kimi-k2-thinking": ("kimi-think", "nvidia"),
    "nvidia/nemotron-super-3": ("nemotron",   "nvidia"),
    "zilf-max":                ("llama-3.3-70b-versatile", "groq"),
}

FALLBACK_CHAIN: list[str] = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "nvidia/deepseek-v3.2",
    "nvidia/qwen-3.5",
    "nvidia/mistral-small-4",
    "nvidia/nemotron-super-3",
]


def resolve_model(model: str) -> tuple[str, str]:
    if model in _FRONTEND_TO_ZILFMAX:
        return _FRONTEND_TO_ZILFMAX[model]
    if model in ZILF_MAX_MODELS:
        model_id = ZILF_MAX_MODELS[model]
        provider = "groq" if model_id in _GROQ_MODELS else "nvidia"
        return model_id, provider
    if model in _GROQ_MODELS:
        return model, "groq"
    return model, "groq"


class ZilfMaxBase(BaseLLM):
    """
    Base class dengan _call_api dan _build_payload yang di-share
    oleh semua mode (chat, agent, thinking).
    """

    def __init__(self, model: str = "groq", max_tokens: int = 4096, temperature: float = 0.7) -> None:
        model_id, provider = resolve_model(model)
        super().__init__(model=model_id, max_tokens=max_tokens, temperature=temperature)
        self._model_key    = model_id
        self._provider     = provider
        self._active_model = model
        self._api_key      = os.environ.get("ZILF_MAX_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("ZILF_MAX_API_KEY environment variable is not set.")
        logger.info(f"[zilf-max] provider={self._provider} model={self._model_key} mode={self.__class__.__name__}")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, messages: list[dict], system: str | None = None) -> dict:
        """
        Build payload untuk zilf-max-api.

        B20 FIX: System prompt di-inject sebagai pesan system terpisah
        di awal history, bukan di-prepend ke content user/assistant.
        Ini mencegah prompt leaking jika model echo back content.
        """
        history: list[dict] = []
        user_message = ""

        for msg in messages:
            if msg["role"] not in ("user", "assistant"):
                continue
            content = msg["content"]
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            parts.append(f"[Tool: {block.get('name')}] {json.dumps(block.get('input', {}))}")
                        elif block.get("type") == "tool_result":
                            parts.append(f"[Result] {block.get('content', '')}")
                    else:
                        parts.append(str(block))
                content = "\n".join(parts)
            history.append({"role": msg["role"], "content": str(content)})

        if history and history[-1]["role"] == "user":
            user_message = history[-1]["content"]
            history = history[:-1]

        # B20 FIX: System prompt sebagai system message terpisah
        # Tidak lagi di-prepend ke history[0] atau user_message
        if system:
            history.insert(0, {"role": "system", "content": system})

        MAX_HISTORY_CHARS = 12_000

        # Pisahkan system prompt dari history biasa
        system_msgs = [h for h in history if h["role"] == "system"]
        user_msgs   = [h for h in history if h["role"] != "system"]

        # Hitung HANYA dari user/assistant messages
        total_chars = sum(len(h["content"]) for h in user_msgs)
        if total_chars > MAX_HISTORY_CHARS:
            # Truncate HANYA user/assistant, system prompt tetap aman
            user_msgs = user_msgs[-4:]
            logger.warning(
                f"[zilf-max] History truncated to last 4 turns "
                f"(was {total_chars} chars total)"
            )

        # Gabungkan kembali: system prompt selalu di depan
        history = system_msgs + user_msgs

        return {
            "message":    user_message,
            "session_id": f"zilf-agent-{uuid.uuid4().hex[:8]}",
            "history":    history,
            "provider":   self._provider,
            "model":      self._model_key,
        }

    async def _call_api(self, payload: dict) -> str:
        """Call zilf-max-api dan return reply text."""
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{ZILF_MAX_BASE_URL}/v1/chat",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code != 200:
                logger.error(f"[zilf-max] {resp.status_code} â€” body: {resp.text[:1000]}")
            resp.raise_for_status()
            return resp.json().get("reply", "")

    async def _call_api_tracked(self, payload: dict) -> tuple[str, int, int]:
        """
        B18 FIX: Call API dan return (reply, input_tokens, output_tokens).

        Estimasi token count berdasarkan karakter jika API tidak return usage.
        Ratio ~4 chars per token (English average).
        """
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{ZILF_MAX_BASE_URL}/v1/chat",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code != 200:
                logger.error(f"[zilf-max] {resp.status_code} â€” body: {resp.text[:1000]}")
            resp.raise_for_status()

            data = resp.json()
            reply = data.get("reply", "")

            # Coba ambil token usage dari response API
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)

            # Fallback: estimasi dari karakter jika API tidak return usage
            if not input_tokens:
                input_text = payload.get("message", "") + str(payload.get("history", []))
                input_tokens = max(1, len(input_text) // 4)
            if not output_tokens:
                output_tokens = max(1, len(reply) // 4)

            return reply, input_tokens, output_tokens

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in [
            "rate limit", "rate_limit", "ratelimit",
            "429", "quota", "too many requests",
            "tpd", "tpm", "capacity", "overloaded", "503",
        ])

    def _is_auth_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in [
            "401", "403",
            "unauthorized", "forbidden",
            "authentication failed", "auth failed",
            "invalid api key", "invalid_api_key",
            "permission", "access denied",
        ])

    def _is_model_not_found_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in [
            "model_not_found",
            "does not exist",
            "not found",
            "invalid model",
        ])

    async def _call_api_with_fallback(
        self, payload: dict, per_model_timeout: float = 15.0, track_tokens: bool = False
    ) -> str | tuple[str, int, int]:
        """
        Call API dengan fallback chain.

        B18 FIX: Tambah parameter track_tokens.
        Jika True, return (reply, input_tokens, output_tokens).
        Jika False, return reply string saja (backward compatible).
        """
        # Tentukan starting point di chain
        if self._active_model in FALLBACK_CHAIN:
            start_idx = FALLBACK_CHAIN.index(self._active_model)
            queue = FALLBACK_CHAIN[start_idx:]
        else:
            queue = FALLBACK_CHAIN

        last_error: Exception | None = None

        for frontend_key in queue:
            model_id, provider = resolve_model(frontend_key)
            patched = {**payload, "model": model_id, "provider": provider}
            try:
                logger.info(f"[zilf-max] trying: {frontend_key}")

                if track_tokens:
                    result = await asyncio.wait_for(
                        self._call_api_tracked(patched),
                        timeout=per_model_timeout,
                    )
                else:
                    result = await asyncio.wait_for(
                        self._call_api(patched),
                        timeout=per_model_timeout,
                    )

                if frontend_key != self._active_model:
                    logger.warning(f"[zilf-max] fallback: {self._active_model} â†’ {frontend_key}")
                    self._active_model = frontend_key
                return result

            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning(
                    f"[zilf-max] timeout on {frontend_key} after {per_model_timeout}s, next..."
                )
                continue
            except Exception as exc:
                last_error = exc
                if self._is_rate_limit_error(exc) or self._is_auth_error(exc) or self._is_model_not_found_error(exc):
                    logger.warning(f"[zilf-max] retryable error on {frontend_key}, next...")
                    continue
                raise

        raise RuntimeError(f"Semua model fallback gagal. Error terakhir: {last_error}")
