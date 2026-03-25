"""
HAMS-MAX Agent Mode — ReAct tool calling via prompt engineering.
TIDAK ada extended thinking di sini — fokus ke format XML saja.
Dipakai oleh reasoning_loop.py saat agent berjalan.

Fixes applied:
  B18 — Token tracking via track_tokens=True
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
_REACT_SYSTEM = """{base_system}

## TOOL CALLING FORMAT (WAJIB IKUTI PERSIS)

Untuk memanggil tool:
<thought>Alasan mengapa tool ini diperlukan</thought>
<action>tool_call</action>
<tool>nama_tool_persis</tool>
<args>{{"param": "value"}}</args>

Untuk final answer (HANYA setelah task selesai 100%):
<thought>Task selesai karena semua langkah sudah diverifikasi</thought>
<action>final_answer</action>
<answer>Jawaban lengkap terstruktur di sini</answer>

## FORMAT JAWABAN FINAL (WAJIB)

Setiap <answer> HARUS ditulis dalam format Markdown terstruktur:
- Gunakan ## untuk heading utama, ### untuk sub-heading
- Gunakan **bold** untuk hal penting, `code` untuk nama file/command
- Gunakan ``` code block ``` dengan syntax highlighting untuk semua kode
- Gunakan numbered list (1. 2. 3.) untuk langkah-langkah urutan
- Gunakan bullet points untuk daftar tanpa urutan
- Tampilkan SEMUA file yang dibuat/diubah beserta isinya dalam code block
- Akhiri dengan bagian **Langkah Selanjutnya** berisi instruksi untuk user

Yang DILARANG ada di dalam <answer>:
- Nama tool mentah (list_dir, run_command, write_file, dll)
- JSON mentah ({{"path": "..."}}, dll)
- Isi <thought> atau proses berpikir
- Tag XML apapun (<tool>, <args>, <action>, dll)
- Teks kosong atau baris yang hanya berisi spasi

Contoh format <answer> yang BENAR:
<answer>
## Hasil: Project FastAPI Berhasil Dibuat

Semua file telah disimpan di `/workspace/my-project/`.

### File yang Dibuat

**`main.py`**
```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def root():
    return {{"message": "Hello World"}}
```

**`requirements.txt`**
```
fastapi==0.104.0
uvicorn==0.24.0
```

### Langkah Selanjutnya
1. Install dependencies: `pip install -r requirements.txt`
2. Jalankan server: `uvicorn main:app --reload`
3. Buka dokumentasi: `http://localhost:8000/docs`
</answer>

## ATURAN KETAT
- SELALU gunakan XML tags persis seperti format di atas
- JANGAN tulis nama tool di luar tag <tool>
- JANGAN tulis JSON di luar tag <args>
- JANGAN beri final_answer sebelum semua file dibuat DAN diverifikasi dengan run_command
- Hanya SATU tool call per respons
- Jika tool gagal karena dependency missing → jalankan pip install dulu, lalu retry

## DAFTAR TOOL TERSEDIA
{tools_text}"""


def _format_tools_text(tools: list[dict]) -> str:
    lines = []
    for t in tools:
        name = t.get("name", "")
        desc = t.get("description", "").split("\n")[0].split(".")[0]
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


def _clean_answer(text: str) -> str:
    text = re.sub(r'<thought>.*?</thought>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<action>.*?</action>',   '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<tool>.*?</tool>',       '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<args>.*?</args>',       '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'^\[Tool[^\]]*\][^\n]*\n?', '', text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'^\s*\{[^}]*\}\s*$', '', text, flags=re.MULTILINE)
    # Strip bare tool names on their own line (e.g. "list_dir\n", "run_command\n")
    text = re.sub(r'^\s*[a-z][a-z0-9_]+\s*$', '', text, flags=re.MULTILINE)
    # Strip lines that are only JSON (handles nested braces too)
    text = re.sub(r'^\s*\{.*?\}\s*$', '', text, flags=re.MULTILINE | re.DOTALL)
    return text.strip()

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

    # CASE 4: Fallback — extract only text outside XML blocks
    stripped = re.sub(
        r'<(thought|action|tool|args)>.*?</\1>',
        '',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    clean_text = _clean_answer(stripped)

    if not clean_text:
        logger.warning("[hams-max] Answer empty after cleaning, returning raw text")
        clean_text = text.strip()

    return thought, "final_answer", clean_text, None


class HamsMaxAgentLLM(HamsMaxBase):
    """
    Mode agent — ReAct tool calling murni.
    B18 FIX: token tracking terintegrasi.
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
            payload = self._build_payload(messages, system=system)
            # B18 FIX: track tokens
            raw_text, input_tokens, output_tokens = await self._call_api_with_fallback(
                payload, track_tokens=True
            )
            return LLMResponse(
                thought=raw_text,
                action_type=ActionType.FINAL_ANSWER,
                tool_calls=[],
                final_answer=raw_text,
                raw=raw_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        # Build ReAct system prompt
        react_system = _REACT_SYSTEM.format(
            base_system=system or "",
            tools_text=_format_tools_text(tools),
        )

        payload = self._build_payload(messages, system=react_system)
        # B18 FIX: track tokens
        raw_text, input_tokens, output_tokens = await self._call_api_with_fallback(
            payload, track_tokens=True
        )

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
                action_type=ActionType.TOOL_CALL,
                tool_calls=[tc],
                final_answer=None,
                raw=raw_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        answer = tool_or_answer or thought or raw_text
        logger.debug(f"[hams-max/agent] → final_answer: {answer[:200]}")
        return LLMResponse(
            thought=thought,
            action_type=ActionType.FINAL_ANSWER,
            tool_calls=[],
            final_answer=answer,
            raw=raw_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def generate_text(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> str:
        payload = self._build_payload(messages, system=system)
        return await self._call_api_with_fallback(payload)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        result = await self.generate(messages, system=system)
        yield result.final_answer or ""