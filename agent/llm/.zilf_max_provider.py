"""
ZILF-MAX LLM Provider â€” wraps zilf-max-api-production.up.railway.app

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

ZILF_MAX_BASE = "https://zilf-max-api-production.up.railway.app"

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

_FRONTEND_TO_HAMSMAX: dict[str, tuple[str, str]] = {
    "llama-3.3-70b-versatile": ("llama-3.3-70b-versatile", "groq"),
    "llama-3.1-8b-instant":    ("llama-3.1-8b-instant",    "groq"),
    "llama3-8b-8192":          ("llama-3.1-8b-instant",    "groq"),   # deprecated â†’ redirect
    "llama3-70b-8192":         ("llama-3.3-70b-versatile", "groq"),   # deprecated â†’ redirect
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
    "zilf-max":                ("llama-3.3-70b-versatile", "groq"),
}

# â”€â”€ System prompts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# FIX: _REACT_SYSTEM sekarang tipis â€” hanya wrapper format XML.
# base_system dari reasoning_loop.py diletakkan di ATAS agar tidak tertimpa.
_REACT_SYSTEM = """{base_system}

## TOOL CALLING FORMAT (WAJIB IKUTI PERSIS)

Untuk memanggil tool:
<thought>Alasan mengapa tool ini diperlukan</thought>
<action>tool_call</action>
<tool>nama_tool_persis</tool>
<args>{{"param": "value"}}</args>

Untuk final answer (HANYA setelah task selesai 100%):
<thought>Task selesai karena...</thought>
<action>final_answer</action>
<answer>Jawaban lengkap di sini</answer>

## ATURAN KETAT
- SELALU gunakan XML tags persis seperti di atas
- JANGAN tulis nama tool di luar tag <tool>
- JANGAN tulis JSON di luar tag <args>
- JANGAN beri final_answer sebelum menggunakan tool yang diperlukan
- Satu tool call per respons
- <answer> hanya boleh berisi teks jawaban, BUKAN tool call

## DAFTAR TOOL TERSEDIA
{tools_text}"""

# Extended thinking: inject sebelum format instructions
_EXTENDED_THINKING_PROMPT = """Sebelum menjawab, pikirkan secara mendalam di dalam tag <think>...</think>.
Gunakan ruang berpikir ini untuk:
- Uraikan masalah langkah demi langkah
- Pertimbangkan berbagai pendekatan dan trade-off
- Rencanakan tool mana yang akan digunakan dan dalam urutan apa
- Pastikan jawaban sudah lengkap sebelum memberi final_answer

ATURAN KRITIS untuk Extended Thinking:
- JANGAN tulis tool call di dalam <think> tags
- <think> hanya untuk berpikir/merencanakan, bukan untuk aksi
- Setelah </think>, langsung ikuti format XML yang benar

"""


def _resolve_model(model: str) -> tuple[str, str]:
    if model in _FRONTEND_TO_HAMSMAX:
        return _FRONTEND_TO_HAMSMAX[model]
    if model in ZILF_MAX_MODELS:
        model_id = ZILF_MAX_MODELS[model]
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


# â”€â”€ Pattern deteksi tool call â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_TOOL_CALL_PATTERNS = [
    r'<tool>\s*\S+\s*</tool>',          # <tool>nama_tool</tool>
    r'<args>\s*\{',                      # <args>{...
    r'<action>\s*tool_call\s*</action>', # <action>tool_call</action>
    r'\[Tool:\s*\w',                     # [Tool: nama_tool]
    r'\[Tool\s+\w',                      # [Tool nama_tool]
]

def _looks_like_tool_call(text: str) -> bool:
    """Return True jika text terlihat seperti tool call, bukan final answer."""
    for pattern in _TOOL_CALL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            return True
    return False


def _clean_answer(text: str) -> str:
    """
    Bersihkan teks final answer dari artefak tool call.
    Hapus: [Tool: ...], <tool>...</tool>, <args>...</args>, <action>...</action>, dll.
    """
    # Hapus blok XML tool call
    text = re.sub(r'<thought>.*?</thought>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<action>.*?</action>',   '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<tool>.*?</tool>',       '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<args>.*?</args>',       '', text, flags=re.DOTALL | re.IGNORECASE)

    # Hapus baris [Tool: ...] style
    text = re.sub(r'^\[Tool[^\]]*\][^\n]*\n?', '', text, flags=re.MULTILINE | re.IGNORECASE)

    # Hapus baris yang hanya berisi JSON object (sisa args)
    text = re.sub(r'^\s*\{[^}]*\}\s*$', '', text, flags=re.MULTILINE)

    return text.strip()


def _parse_react_response(text: str) -> tuple[str, str, str | None, dict | None]:
    """
    Parse respons ReAct dari model.

    Return: (thought, action_type, tool_name_or_answer, tool_args_or_none)

    FIX utama:
    1. Kalau tidak ada <answer> tag tapi text mengandung pola tool call â†’ paksa jadi tool_call
    2. Kalau final answer mengandung sisa tool call text â†’ bersihkan dulu
    3. Validasi: kalau answer masih mengandung tool call patterns setelah dibersihkan â†’ retry sebagai tool_call
    """
    thought_m = re.search(r'<thought>(.*?)</thought>', text, re.DOTALL | re.IGNORECASE)
    action_m  = re.search(r'<action>(.*?)</action>',   text, re.DOTALL | re.IGNORECASE)
    tool_m    = re.search(r'<tool>(.*?)</tool>',       text, re.DOTALL | re.IGNORECASE)
    args_m    = re.search(r'<args>(.*?)</args>',       text, re.DOTALL | re.IGNORECASE)
    answer_m  = re.search(r'<answer>(.*?)</answer>',   text, re.DOTALL | re.IGNORECASE)

    thought    = thought_m.group(1).strip() if thought_m else ""
    action_raw = action_m.group(1).strip().lower() if action_m else ""

    # â”€â”€ CASE 1: Eksplisit tool_call â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

    # â”€â”€ CASE 2: Ada <answer> tag â†’ ambil isinya â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if answer_m:
        raw_answer = answer_m.group(1).strip()

        # Validasi: kalau isi <answer> MASIH mengandung tool call â†’ paksa jadi tool_call
        if _looks_like_tool_call(raw_answer) and tool_m:
            tool_name = tool_m.group(1).strip()
            tool_args = {}
            if args_m:
                try:
                    tool_args = json.loads(args_m.group(1).strip())
                except json.JSONDecodeError:
                    pass
            logger.warning(f"[zilf-max] <answer> tag contains tool call text, forcing tool_call: {tool_name}")
            return thought, "tool_call", tool_name, tool_args

        # Bersihkan answer dari sisa-sisa tool call text
        clean = _clean_answer(raw_answer)
        return thought, "final_answer", clean or raw_answer, None

    # â”€â”€ CASE 3: Tidak ada <answer> dan tidak ada action eksplisit â”€â”€â”€â”€â”€â”€â”€â”€
    # Cek apakah keseluruhan text ini sebenarnya adalah tool call
    if _looks_like_tool_call(text):
        # Coba parse sebagai tool call
        if tool_m:
            tool_name = tool_m.group(1).strip()
            tool_args = {}
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
            logger.debug(f"[zilf-max] No <answer> tag, detected tool call: {tool_name}")
            return thought, "tool_call", tool_name, tool_args

    # â”€â”€ CASE 4: Fallback â€” bersihkan text dan jadikan final answer â”€â”€â”€â”€â”€â”€â”€â”€
    # Tapi hanya kalau TIDAK ada tanda-tanda tool call sama sekali
    clean_text = _clean_answer(text)

    # Kalau setelah dibersihkan hasilnya kosong â†’ ada yang salah, return raw
    if not clean_text:
        logger.warning("[zilf-max] Answer empty after cleaning, returning raw text")
        clean_text = text.strip()

    return thought, "final_answer", clean_text, None


def _extract_thinking(text: str) -> tuple[str, str]:
    """
    Pisahkan <think>...</think> dari teks respons.
    Return (thinking_content, answer_without_think_tags).
    """
    think_blocks = re.findall(r'<think>(.*?)</think>', text, re.DOTALL | re.IGNORECASE)
    answer = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    thinking = "\n\n".join(block.strip() for block in think_blocks)
    return thinking, answer


class ZilfMaxLLM(BaseLLM):
    """
    LLM provider yang memanggil ZILF-MAX API.

    Fitur:
        extended=True  â†’ inject extended thinking prompt, parse <think> blocks
        tools=[...]    â†’ ReAct tool calling via prompt engineering
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
        self._api_key   = os.environ.get("ZILF_MAX_API_KEY", "")

        if not self._api_key:
            raise RuntimeError("ZILF_MAX_API_KEY environment variable is not set.")

        logger.info(f"[zilf-max] provider={self._provider} model={self._model_key} extended={self._extended}")

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

            # Flatten content kalau berupa list (tool call, tool result, dll)
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
            "session_id": f"zilf-agent-{uuid.uuid4().hex[:8]}",
            "history":    history,
            "provider":   self._provider,
            "model":      self._model_key,
        }

    async def _call_api(self, payload: dict) -> str:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{ZILF_MAX_BASE}/v1/chat",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json().get("reply", "")

    # â”€â”€ generate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
            # FIX: base_system (dari reasoning_loop) diletakkan PERTAMA di _REACT_SYSTEM
            tools_text   = _format_tools_text(tools)
            react_system = _REACT_SYSTEM.format(
                base_system=system or "",
                tools_text=tools_text,
            )
            if use_extended:
                react_system = _EXTENDED_THINKING_PROMPT + react_system

            payload  = self._build_payload(messages, system=react_system)
            raw_text = await self._call_api(payload)

            logger.debug(f"[zilf-max] Raw response (first 300 chars): {raw_text[:300]}")

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
                logger.debug(f"[zilf-max] â†’ tool_call: {tool_or_answer} args={tool_args}")
                return LLMResponse(
                    thought=thought,
                    action_type=ActionType.TOOL_CALL if hasattr(ActionType, 'TOOL_CALL') else "tool_call",
                    tool_calls=[tc],
                    final_answer=None,
                    raw=raw_text,
                )
            else:
                answer = tool_or_answer or thought or raw_text

                # Validasi final: kalau answer masih mengandung tool call text â†’ log warning
                if _looks_like_tool_call(answer):
                    logger.warning(
                        f"[zilf-max] final_answer still contains tool call patterns after cleaning! "
                        f"Answer (first 200): {answer[:200]}"
                    )
                    # Last resort clean
                    answer = _clean_answer(answer) or answer

                logger.debug(f"[zilf-max] â†’ final_answer (first 200): {answer[:200]}")
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

    # â”€â”€ generate_text â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
        """
        use_extended = extended or self._extended
        full_system  = system or ""

        if use_extended:
            full_system = _EXTENDED_THINKING_PROMPT + full_system

        payload  = self._build_payload(messages, system=full_system if full_system else None)
        raw_text = await self._call_api(payload)

        if use_extended:
            thinking, answer = _extract_thinking(raw_text)
            return json.dumps({
                "thinking": thinking,
                "answer":   answer,
                "raw":      raw_text,
            }, ensure_ascii=False)

        return raw_text

    # â”€â”€ stream â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
                f"{ZILF_MAX_BASE}/v1/chat/stream",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    if chunk:
                        yield chunk