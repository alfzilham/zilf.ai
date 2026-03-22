"""
HTTP API for the Hams AI.

Provides:
  GET  /health           — liveness + readiness probe (used by Docker healthcheck)
  POST /run              — submit a task and get the result
  POST /run/stream       — submit a task and stream the response (Server-Sent Events)
  GET  /status/{run_id} — check status of a running task
  GET  /chat-ui          — web chat interface (multitask AI)
  POST /chat             — chat endpoint for the web UI

Run standalone:
    uvicorn agent.api:app --host 0.0.0.0 --port 8000 --reload

Or via CLI:
    python -m agent.main serve
"""

from __future__ import annotations

import os
import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI(
    title="Hams AI",
    description="Autonomous coding assistant API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_tasks: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    task: str = Field(..., description="The coding task to complete", min_length=1)
    provider: str = Field("ollama", description="LLM provider: ollama | groq | google")
    model: str | None = Field(None, description="Model name — uses provider default if omitted")
    max_steps: int = Field(30, ge=1, le=100)


class RunResponse(BaseModel):
    run_id: str
    status: str
    final_answer: str | None = None
    error: str | None = None
    steps_taken: int = 0
    total_tokens: int = 0
    duration_seconds: float | None = None
    started_at: str = ""
    completed_at: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
    uptime_seconds: float


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

_start_time = time.time()


def _build_agent(request: RunRequest) -> Any:
    from agent.tools.registry import ToolRegistry
    from agent.llm.router import LLMRouter

    registry = ToolRegistry.default()
    try:
        llm = LLMRouter.from_env()
    except RuntimeError:
        from examples.basic_agent import MockLLM
        llm = MockLLM()

    from agent.core.agent import Agent
    return Agent(
        llm=llm,
        tool_registry=registry,
        max_steps=request.max_steps,
        use_planner=True,
        verbose=False,
    )


# ---------------------------------------------------------------------------
# System / Agent endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=round(time.time() - _start_time, 1),
    )


@app.post("/run", response_model=RunResponse, tags=["agent"])
async def run_task(request: RunRequest) -> RunResponse:
    agent = _build_agent(request)
    t0 = time.perf_counter()
    response = await agent.run(request.task)
    elapsed = time.perf_counter() - t0
    _tasks[response.run_id] = {"status": response.status.value, "completed": True}
    return RunResponse(
        run_id=response.run_id,
        status=response.status.value,
        final_answer=response.final_answer,
        error=response.error,
        steps_taken=response.steps_taken,
        total_tokens=response.total_input_tokens + response.total_output_tokens,
        duration_seconds=round(elapsed, 2),
        started_at=response.started_at.isoformat() if response.started_at else "",
        completed_at=response.completed_at.isoformat() if response.completed_at else None,
    )


@app.post("/run/stream", tags=["agent"])
async def run_task_stream(request: RunRequest) -> StreamingResponse:
    import json

    async def event_stream() -> AsyncIterator[str]:
        agent = _build_agent(request)
        original_run_step = agent._loop.run_step

        async def instrumented_step(state: Any) -> Any:
            state = await original_run_step(state)
            latest = state.latest_step()
            if latest:
                tools = [tc.tool_name for tc in latest.tool_calls]
                obs = latest.observations[:200] if latest.observations else ""
                event = {
                    "type": "step",
                    "step": latest.step_number,
                    "thought": latest.thought[:200] if latest.thought else "",
                    "tools": tools,
                    "observation": obs,
                }
                yield f"data: {json.dumps(event)}\n\n"

        response = await agent.run(request.task)
        final_event = {
            "type": "complete" if response.success else "error",
            "run_id": response.run_id,
            "final_answer": response.final_answer,
            "error": response.error,
            "steps": response.steps_taken,
            "tokens": response.total_input_tokens + response.total_output_tokens,
        }
        yield f"data: {json.dumps(final_event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/status/{run_id}", tags=["agent"])
async def get_status(run_id: str) -> dict[str, Any]:
    if run_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Run ID '{run_id}' not found.")
    return _tasks[run_id]


# ---------------------------------------------------------------------------
# /chat-ui  — serve halaman web chat
# ---------------------------------------------------------------------------

@app.get("/chat-ui", tags=["chat"], include_in_schema=False)
async def chat_ui() -> FileResponse:
    """Serve halaman web chat multitask HAMS.AI."""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "chat.html")
    return FileResponse(html_path, media_type="text/html")


# ---------------------------------------------------------------------------
# /chat  — multitask AI endpoint
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str       # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    history: list[ChatMessage] | None = []
    model: str | None = Field(
        default="llama-3.3-70b-versatile",
        description=(
            "Model ID. Groq: llama-3.3-70b-versatile, llama-3.1-8b-instant, "
            "gemma2-9b-it, compound-beta. "
            "NVIDIA: nvidia/llama-3.3-nemotron-super-49b-v1, "
            "nvidia/llama-3.1-nemotron-ultra-253b-v1"
        )
    )

class ChatResponse(BaseModel):
    session_id: str
    response: str
    model_used: str


# System prompt untuk general AI multitask
_SYSTEM_PROMPT = """Kamu adalah HAMS.AI — asisten AI serba bisa yang powerful dan cerdas.

## KEMAMPUAN UTAMA
Kamu bisa melakukan SEMUA hal berikut dengan kualitas tinggi:

1. **Membuat Website & UI**
   - Buat HTML/CSS/JS lengkap yang langsung bisa dijalankan
   - Landing page, dashboard, game web, UI component, portfolio
   - Gunakan teknik modern: flexbox, grid, animasi CSS, gradient, glassmorphism
   - Kode harus COMPLETE, tidak perlu dipotong, tidak perlu "...tambahkan sendiri..."

2. **Generate Kode Program**
   - Python, JavaScript, TypeScript, SQL, Bash, dan bahasa lainnya
   - API backend (FastAPI, Express), script otomasi, algoritma
   - Selalu sertakan komentar yang jelas dan error handling

3. **Menulis Konten**
   - Artikel blog, copywriting, deskripsi produk, caption media sosial
   - Esai, laporan, press release, email profesional
   - Konten dalam bahasa Indonesia maupun Inggris

4. **Analisis & Riset**
   - Bandingkan teknologi, framework, tools
   - Breakdown strategi bisnis, marketing, atau teknis
   - Buat tabel perbandingan yang jelas
   - Jelaskan konsep kompleks dengan mudah dipahami

5. **Brainstorming & Ideasi**
   - Ide konten, nama produk, tagline, konsep desain
   - Solusi masalah teknis maupun non-teknis
   - Roadmap dan perencanaan project

## ATURAN RESPONS
- **Untuk kode HTML/CSS/JS**: selalu tulis LENGKAP dalam satu blok kode, siap digunakan
- **Untuk artikel/konten**: tulis dengan struktur heading yang jelas (##, ###)
- **Untuk analisis**: gunakan tabel dan poin-poin terstruktur
- **Format markdown**: gunakan heading, bold, list, code block, tabel dengan tepat
- **Bahasa**: ikuti bahasa pengguna (Indonesia atau Inggris)
- **Kualitas**: prioritaskan output yang akurat, lengkap, dan langsung bisa dipakai
- Jangan potong kode dengan "// ... tambahkan sendiri" — tulis SEMUA kode
- Jangan tambahkan disclaimer berlebihan, langsung berikan hasilnya"""


@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(req: ChatRequest) -> ChatResponse:
    """
    Endpoint chat multitask — mendukung pembuatan website, kode, artikel, analisis.
    Model bisa dipilih dari Groq atau NVIDIA via parameter `model`.
    """
    import uuid as _uuid
    from agent.llm.router import LLMRouter

    session_id = req.session_id or str(_uuid.uuid4())
    model      = req.model or "llama-3.3-70b-versatile"

    # Tentukan provider berdasarkan model
    is_nvidia = model.startswith("nvidia/")

    # Bangun full prompt: system + history + pesan baru
    history_text = ""
    for msg in (req.history or []):
        prefix = "User" if msg.role == "user" else "Assistant"
        history_text += f"{prefix}: {msg.content}\n"

    full_prompt = f"{_SYSTEM_PROMPT}\n\n{history_text}User: {req.message}\nAssistant:"

    try:
        hams_key = os.environ.get("HAMS_MAX_API_KEY")

        if hams_key:
            from agent.llm.hams_max_provider import HamsMaxLLM
            # Pass model langsung ke HAMS-MAX provider
            llm = HamsMaxLLM(model=model)
        else:
            llm = LLMRouter.from_env()

        messages = [{"role": "user", "content": full_prompt}]
        reply = await llm.generate_text(
            messages=messages,
            max_tokens=4096,   # lebih besar untuk output panjang (website, artikel)
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ChatResponse(
        session_id=session_id,
        response=reply.strip(),
        model_used=model,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="hams.ai API Server")
    parser.add_argument("--port",   type=int, default=int(os.environ.get("AGENT_PORT", 8000)))
    parser.add_argument("--host",   type=str, default=os.environ.get("AGENT_HOST", "127.0.0.1"))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "agent.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",
    )