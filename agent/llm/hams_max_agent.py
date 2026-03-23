"""
HAMS-MAX Agent Mode — ReAct tool calling via prompt engineering.
TIDAK ada extended thinking di sini — fokus ke format XML saja.
Dipakai oleh reasoning_loop.py saat agent berjalan.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, AsyncIterator

from loguru import logger

from agent.llm.hams_max_base import HamsMaxBase
from agent.llm.base import LLMResponse

# ── ReAct system prompt ────────────────────────────────────────────────────
_REACT_SYSTEM = """## TOOL CALLING FORMAT — IKUTI PERSIS

Untuk memanggil tool:
<thought>Alasan mengapa tool ini diperlukan</thought>
<action>tool_call</action>
<tool>nama_tool_persis</tool>
<args>{{"param": "value"}}</args>

Untuk final answer (HANYA setelah task 100% selesai):
<thought>Task selesai karena...</thought>
<action>final_answer</action>
<answer>Jawaban lengkap di sini</answer>

## ATURAN KETAT
- SELALU gunakan XML tags persis seperti di atas
- JANGAN tulis nama tool di luar tag <tool>
- JANGAN beri final_answer sebelum menggunakan minimal satu tool
- Satu tool call per respons
- <answer> hanya berisi teks, BUKAN tool call

## DAFTAR TOOL
{tools_text}"""


def _format_tools_text(tools: list[dict]) -> str:
    lines = []
    for t in tools:
        name = t.get("name", "")
        desc = t.get("description", "").split("\n")[0].split(".")[0]
        lines.append(f"- {name}: {desc}")
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

    raw_answer = (answer_m.group(1).strip() if answer_m else None) or text.strip()
    answer = re.sub(r'\[Tool[^\]]*\][^\n]*\n?', '', raw_answer).strip()
    return thought, "final_answer", answer or raw_answer, None


class HamsMaxAgentLLM(HamsMaxBase):
    """
    Mode agent — ReAct tool calling murni.
    Extended thinking TIDAK diaktifkan di sini untuk mencegah
    payload overflow dan konflik format XML.
    """

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        from agent.core.state import ToolCall, ActionType

        if not tools:
            # Fallback ke simple generate kalau tidak ada tools
            payload  = self._build_payload(messages, system=system)
            raw_text = await self._call_api_with_fallback(payload)
            return LLMResponse(
                thought=raw_text,
                action_type=ActionType.FINAL_ANSWER if hasattr(ActionType, 'FINAL_ANSWER') else "final_answer",
                tool_calls=[],
                final_answer=raw_text,
                raw=raw_text,
            )

        # Build ReAct system prompt — tanpa extended thinking
        react_system = _REACT_SYSTEM.format(
            tools_text=_format_tools_text(tools),
        )

        payload  = self._build_payload(messages, system=react_system)
        raw_text = await self._call_api_with_fallback(payload)

        logger.debug(f"[hams-max/agent] Raw (first 300): {raw_text[:300]}")

        thought, action_type, tool_or_answer, tool_args = _parse_react_response(raw_text)

        if action_type == "tool_call" and tool_or_answer:
            tc = ToolCall(
                tool_name=tool_or_answer,
                tool_use_id=f"tc_{uuid.uuid4().hex[:8]}",
                tool_input=tool_args or {},
            )
            logger.debug(f"[hams-max/agent] → tool_call: {tool_or_answer}")
            return LLMResponse(
                thought=thought,
                action_type=ActionType.TOOL_CALL if hasattr(ActionType, 'TOOL_CALL') else "tool_call",
                tool_calls=[tc],
                final_answer=None,
                raw=raw_text,
            )

        answer = tool_or_answer or thought or raw_text
        logger.debug(f"[hams-max/agent] → final_answer: {answer[:200]}")
        return LLMResponse(
            thought=thought,
            action_type=ActionType.FINAL_ANSWER if hasattr(ActionType, 'FINAL_ANSWER') else "final_answer",
            tool_calls=[],
            final_answer=answer,
            raw=raw_text,
        )

    async def generate_text(self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any) -> str:
        payload = self._build_payload(messages, system=system)
        return await self._call_api(payload)

    async def stream(self, messages: list[dict[str, Any]], system: str | None = None, **kwargs: Any):
        result = await self.generate(messages, system=system)
        yield result.final_answer or ""
