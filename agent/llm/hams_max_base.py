"""
HAMS-MAX Base — shared constants, helpers, dan base class.
Di-import oleh hams_max_chat.py, hams_max_agent.py, hams_max_thinking.py.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
from loguru import logger

from agent.llm.base import BaseLLM, LLMResponse

HAMS_MAX_BASE_URL = "https://hams-max-api-production.up.railway.app"

HAMS_MAX_MODELS: dict[str, str] = {
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

_FRONTEND_TO_HAMSMAX: dict[str, tuple[str, str]] = {
    "llama-3.3-70b-versatile": ("llama-3.3-70b-versatile", "groq"),
    "llama-3.1-8b-instant":    ("llama-3.1-8b-instant",    "groq"),
    "llama3-8b-8192":          ("llama-3.1-8b-instant",    "groq"),
    "llama3-70b-8192":         ("llama-3.3-70b-versatile", "groq"),
    "gemma2-9b-it":            ("gemma2-9b-it",             "groq"),
    "compound-beta":           ("compound-beta",            "groq"),
    "nvidia/qwen-3.5":         ("qwen",       "nvidia"),
    "nvidia/glm-5":            ("glm",        "nvidia"),
    "nvidia/minimax-m25":      ("minimax",    "nvidia"),
    "nvidia/kimi-k2.5":        ("kimi",       "nvidia"),
    "nvidia/stepfun-step3.5":  ("step",       "nvidia"),
    "nvidia/mistral-small-4":  ("mistral",    "nvidia"),
    "nvidia/qwen-397b":        ("qwen397b",   "nvidia"),
    "nvidia/deepseek-v3.2":    ("deepseek",   "nvidia"),
    "nvidia/kimi-k2-thinking": ("kimi-think", "nvidia"),
    "nvidia/nemotron-super-3": ("nemotron",   "nvidia"),
    "hams-max":                ("llama-3.3-70b-versatile", "groq"),
}


def resolve_model(model: str) -> tuple[str, str]:
    if model in _FRONTEND_TO_HAMSMAX:
        return _FRONTEND_TO_HAMSMAX[model]
    if model in HAMS_MAX_MODELS:
        model_id = HAMS_MAX_MODELS[model]
        provider = "groq" if model_id in _GROQ_MODELS else "nvidia"
        return model_id, provider
    if model in _GROQ_MODELS:
        return model, "groq"
    return model, "groq"


class HamsMaxBase(BaseLLM):
    """
    Base class dengan _call_api dan _build_payload yang di-share
    oleh semua mode (chat, agent, thinking).
    """

    def __init__(self, model: str = "groq", max_tokens: int = 4096, temperature: float = 0.7) -> None:
        model_id, provider = resolve_model(model)
        super().__init__(model=model_id, max_tokens=max_tokens, temperature=temperature)
        self._model_key = model_id
        self._provider  = provider
        self._api_key   = os.environ.get("HAMS_MAX_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("HAMS_MAX_API_KEY environment variable is not set.")
        logger.info(f"[hams-max] provider={self._provider} model={self._model_key} mode={self.__class__.__name__}")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, messages: list[dict], system: str | None = None) -> dict:
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

        if system and history:
            history[0]["content"] = f"{system}\n\n{history[0]['content']}"
        elif system:
            user_message = f"{system}\n\n{user_message}"

        return {
            "message":    user_message,
            "session_id": f"hams-agent-{uuid.uuid4().hex[:8]}",
            "history":    history,
            "provider":   self._provider,
            "model":      self._model_key,
        }

    async def _call_api(self, payload: dict) -> str:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{HAMS_MAX_BASE_URL}/v1/chat",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code != 200:
                logger.error(f"[hams-max] {resp.status_code} — body: {resp.text[:1000]}")
            resp.raise_for_status()
            return resp.json().get("reply", "")
