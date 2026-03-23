"""
HAMS-MAX LLM Provider — wraps hams-max-api-production.up.railway.app

Fitur:
- ReAct-style tool calling via prompt engineering (XML tags)
- Extended Thinking: AI menulis proses berpikirnya di <think>...</think>
- Auto-detect Groq vs NVIDIA provider dari model ID
- Streaming support untuk Groq models
- Backward compatible dengan shorthand alias
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, AsyncIterator

import httpx
from loguru import logger

from agent.llm.base import BaseLLM, LLMResponse

HAMS_MAX_BASE = "https://hams-max-api-production.up.railway.app"

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
    "llama3-8b-8192":          ("llama3-8b-8192",           "groq"),
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
    "hams-max":                ("deepseek",   "nvidia"),
}

# ── System prompts ─────────────────────────────────────────────────────────

_REACT_SYSTEM = """You are an autonomous AI agent. Complete tasks step-by-step using tools.

## RESPONSE FORMAT

Tool call:
<thought>Your reasoning</thought>
<action>tool_call</action>
<tool>exact_tool_name</tool>
<args>{{"param": "value"}}</args>

Final answer:
<thought>Your reasoning</thought>
<action>final_answer</action>
<answer>Your complete answer</answer>

## RULES
- Use EXACT tool names. Args must be valid JSON. One tool per response.
- Use final_answer when task is done.

## AVAILABLE TOOLS
{tools_text}

{base_system}"""

# Extended thinking: minta model berpikir keras sebelum menjawab
_EXTENDED_THINKING_PROMPT = """Before answering, think deeply and thoroughly inside <think>...</think> tags.
Use this thinking space to:
- Break down the problem step by step
- Consider multiple approaches and their tradeoffs  
- Reason through edge cases and potential issues
- Plan your response structure

After thinking, provide your final answer outside the <think> tags.

<think> blocks will be shown to the user as your reasoning process.
Be thorough in your thinking — more thinking leads to better answers.

"""


def _resolve_model(model: str) -> tuple[str, str]:
    if model in _FRONTEND_TO_HAMSMAX:
        return _FRONTEND_TO_HAMSMAX[model]
    if model in HAMS_MAX_MODELS:
        model_id = HAMS_MAX_MODELS[model]
        provider = "groq" if model_id in _GROQ_MODELS else "nvidia"
        return model_id, provider
    if model in _GROQ_MODELS:
        return model, "groq"
    return model, "groq"


def _format_tools_text(tools: list[dict]) -> str:
    lines = []
    for t in tools:
        name     = t.get("name", "")
        desc     = t.get("description", "")
        schema   = t.get("input_schema", {})
        props    = schema.get("properties", {})
        required = schema.get("required", [])
        params   = [
            f"  {'*' if p in required else '?'} {p} ({info.get('type','str')}): {info.get('description','')}"
            for p, info in props.items()
        ]
        lines += [f"### {name}", f"Description: {desc}"]
        if params:
            lines += ["Parameters (* = required):"] + params
        lines.append("")
    return "\n".join(lines)


def _parse_react_response(text: str) -> tuple[str, str, str | None, dict | None]:
    thought_m = re.search(r'<thought>(.*?)</thought>', text, re.DOTALL | re.IGNORECASE)
    action_m  = re.search(r'<action>(.*?)</action>',   text, re.DOTALL | re.IGNORECASE)
    tool_m    = re.search(r'<tool>(.*?)</tool>',       text, re.DOTALL | re.IGNORECASE)
    args_m    = re.search(r'<args>(.*?)</args>',       text, re.DOTALL | re.IGNORECASE)
    answer_m  = re.search(r'<answer>(.*?)</answer>',   text, re.DOTALL | re.IGNORECASE)

    thought    = thought_m.group(1).strip() if thought_m else ""
    action_raw = action_m.group(1).strip().lower() if action_m else "final_answer"

    if action_raw == "tool_call" and tool_m:
        tool_name = tool_m.group(1).strip()
        tool_args: dict = {}
        if args_m:
            try:
                tool_args = json.loads(args_m.group(1).strip())
            except json.JSONDecodeError:
                m = re.search(r'\{.*\}', args_m.group(1), re.DOTALL)
                if m:
                    try:
                        tool_args = json.loads(m.group())
                    except json.JSONDecodeError:
                        pass
        return thought, "tool_call", tool_name, tool_args

    answer = (answer_m.group(1).strip() if answer_m else None) or text.strip()
    return thought, "final_answer", answer, None


def _extract_thinking(text: str) -> tuple[str, str]:
    """
    Pisahkan <think>...</think> dari teks respons.
    Return (thinking_content, answer_without_think_tags).
    """
    think_blocks = re.findall(r'<think>(.*?)</think>', text, re.DOTALL | re.IGNORECASE)
    answer = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    thinking = "\n\n".join(block.strip() for block in think_blocks)
    return thinking, answer


class HamsMaxLLM(BaseLLM):
    """
    LLM provider yang memanggil HAMS-MAX API.

    Fitur baru:
        extended=True  → inject extended thinking prompt, parse <think> blocks
        tools=[...]    → ReAct tool calling via prompt engineering
    """

    def __init__(
        self,
        model: str = "groq",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        extended: bool = False,
    ) -> None:
        model_id, provider = _resolve_model(model)
        super().__init__(model=model_id, max_tokens=max_tokens, temperature=temperature)

        self._model_key = model_id
        self._provider  = provider
        self._extended  = extended
        self._api_key   = os.environ.get("HAMS_MAX_API_KEY", "")

        if not self._api_key:
            raise RuntimeError("HAMS_MAX_API_KEY environment variable is not set.")

        logger.info(f"[hams-max] provider={self._provider} model={self._model_key} extended={self._extended}")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, messages: list[dict], system: str | None = None) -> dict:
        history: list[dict] = []
        user_message = ""

        for msg in messages:
            if msg["role"] in ("user", "assistant"):
                history.append({"role": msg["role"], "content": msg["content"]})

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
                f"{HAMS_MAX_BASE}/v1/chat",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json().get("reply", "")

    # ── generate ──────────────────────────────────────────────────────────

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        extended: bool | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        from agent.core.state import ToolCall, ActionType

        use_extended = extended if extended is not None else self._extended

        if tools:
            # Agentic ReAct mode
            tools_text   = _format_tools_text(tools)
            react_system = _REACT_SYSTEM.format(
                tools_text=tools_text,
                base_system=system or "",
            )
            if use_extended:
                react_system = _EXTENDED_THINKING_PROMPT + react_system

            payload  = self._build_payload(messages, system=react_system)
            raw_text = await self._call_api(payload)

            # Extract thinking if present
            thinking, clean_text = _extract_thinking(raw_text)
            thought, action_type, tool_or_answer, tool_args = _parse_react_response(clean_text)

            if not thought and thinking:
                thought = thinking  # use thinking as thought for display

            if action_type == "tool_call" and tool_or_answer:
                tc = ToolCall(
                    tool_name=tool_or_answer,
                    tool_use_id=f"tc_{uuid.uuid4().hex[:8]}",
                    tool_input=tool_args or {},
                )
                return LLMResponse(
                    thought=thought,
                    action_type=ActionType.TOOL_CALL if hasattr(ActionType, 'TOOL_CALL') else "tool_call",
                    tool_calls=[tc],
                    final_answer=None,
                    raw=raw_text,
                )
            else:
                answer = tool_or_answer or thought or raw_text
                return LLMResponse(
                    thought=thought,
                    action_type=ActionType.FINAL_ANSWER if hasattr(ActionType, 'FINAL_ANSWER') else "final_answer",
                    tool_calls=[],
                    final_answer=answer,
                    raw=raw_text,
                )

        else:
            # Simple chat mode
            full_system = system or ""
            if use_extended:
                full_system = _EXTENDED_THINKING_PROMPT + full_system

            payload  = self._build_payload(messages, system=full_system if full_system else None)
            raw_text = await self._call_api(payload)

            return LLMResponse(
                thought=raw_text,
                action_type="final_answer",
                tool_calls=[],
                final_answer=raw_text,
                raw=raw_text,
            )

    # ── generate_text ──────────────────────────────────────────────────────

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        extended: bool = False,
        **kwargs: Any,
    ) -> str:
        """
        Simple text generation.
        Jika extended=True, return dict {"thinking": "...", "answer": "..."} sebagai JSON string.
        Kalau tidak, return string biasa.
        """
        use_extended = extended or self._extended
        full_system  = system or ""

        if use_extended:
            full_system = _EXTENDED_THINKING_PROMPT + full_system

        payload  = self._build_payload(messages, system=full_system if full_system else None)
        raw_text = await self._call_api(payload)

        if use_extended:
            thinking, answer = _extract_thinking(raw_text)
            # Return JSON string supaya api.py bisa parse
            return json.dumps({
                "thinking": thinking,
                "answer":   answer,
                "raw":      raw_text,
            }, ensure_ascii=False)

        return raw_text

    # ── stream ─────────────────────────────────────────────────────────────

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        if self._provider != "groq":
            result = await self.generate(messages, system=system)
            yield result.final_answer or ""
            return

        payload = self._build_payload(messages, system=system)
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream(
                "POST",
                f"{HAMS_MAX_BASE}/v1/chat/stream",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    if chunk:
                        yield chunk