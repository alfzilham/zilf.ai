"""
HTTP API for the Hams AI — v0.3.0

Endpoints:
  GET  /health            — liveness probe
  GET  /chat-ui           — web chat interface
  POST /chat              — multitask chat (simple + extended thinking)
  POST /agent/run         — agentic run, blocking
  POST /agent/stream      — agentic run, real-time SSE
  POST /run               — legacy agent run (blocking)
  POST /run/stream        — legacy agent run (SSE)
  GET  /status/{run_id}   — check task status
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# App setup — urutan PENTING: buat app → middleware → mount static
# ---------------------------------------------------------------------------

app = FastAPI(title="Hams AI", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC_DIR    = os.path.join(os.path.dirname(__file__), "static")
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# ---------------------------------------------------------------------------

_tasks: dict[str, dict[str, Any]] = {}
_start_time = time.time()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
    uptime_seconds: float


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    history: list[ChatMessage] | None = []
    model: str | None = Field(default="llama-3.3-70b-versatile")
    extended: bool = Field(
        default=False,
        description="Aktifkan Extended Thinking — AI menampilkan proses berpikirnya sebelum menjawab"
    )


class ChatResponse(BaseModel):
    session_id: str
    response: str
    thinking: str | None = None
    model_used: str
    extended: bool


class AgentRunRequest(BaseModel):
    task: str = Field(..., min_length=1)
    model: str | None = Field(default="llama-3.3-70b-versatile")
    max_steps: int = Field(15, ge=1, le=50)
    extended: bool = Field(default=False)


class AgentStepInfo(BaseModel):
    step: int
    thought: str
    tools_called: list[dict]
    tool_results: list[dict]
    is_final: bool = False


class AgentRunResponse(BaseModel):
    run_id: str
    status: str
    final_answer: str | None = None
    error: str | None = None
    steps: list[AgentStepInfo]
    steps_taken: int
    duration_seconds: float | None = None
    model_used: str


class RunRequest(BaseModel):
    task: str = Field(..., min_length=1)
    provider: str = Field("ollama")
    model: str | None = None
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MULTITASK_SYSTEM = """Kamu adalah HAMS.AI — asisten AI serba bisa yang powerful dan cerdas.

## KEMAMPUAN UTAMA
1. **Website & UI** — HTML/CSS/JS lengkap, landing page, dashboard, game web, animasi
2. **Kode Program** — Python, JS, SQL, Bash, API, algoritma, lengkap dengan komentar
3. **Konten** — Artikel, blog, copywriting, esai, email profesional
4. **Analisis** — Perbandingan teknologi, strategi, tabel, breakdown konsep kompleks

## ATURAN
- Kode HTML/CSS/JS: tulis LENGKAP dalam satu blok, siap digunakan
- Artikel/konten: gunakan heading yang jelas
- Tulis SEMUA kode — jangan potong dengan "// ... tambahkan sendiri"
- Ikuti bahasa pengguna (Indonesia atau Inggris)
- Langsung berikan hasilnya"""


def _build_llm(model: str, extended: bool = False) -> Any:
    # Ollama — local
    if model.startswith("ollama/"):
        try:
            from agent.llm.ollama_provider import OllamaLLM
            return OllamaLLM(model=model.replace("ollama/", ""))
        except Exception:
            pass

    # Google Gemini
    if model.startswith("gemini-"):
        google_key = os.environ.get("GOOGLE_API_KEY")
        if google_key:
            try:
                from agent.llm.google_provider import GoogleLLM
                return GoogleLLM(model=model)
            except Exception:
                pass

    # Groq — langsung ke Groq API
    _GROQ_IDS = {
        "llama3-70b-8192", "mixtral-8x7b-32768", "gemma2-9b-it",
        "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "compound-beta"
    }
    if model in _GROQ_IDS:
        groq_key = os.environ.get("GROQ_API_KEY")
        if groq_key:
            try:
                from agent.llm.groq_provider import GroqLLM
                return GroqLLM(model=model)
            except Exception:
                pass

    # NVIDIA & hams-max — lewat HAMS-MAX API
    if model.startswith("nvidia/") or model == "hams-max":
        hams_key = os.environ.get("HAMS_MAX_API_KEY")
        if hams_key:
            from agent.llm.hams_max_provider import HamsMaxLLM
            return HamsMaxLLM(model=model, extended=extended)

    # Fallback
    from agent.llm.router import LLMRouter
    return LLMRouter.from_env()


def _build_agent(model: str, max_steps: int, step_callback=None, extended: bool = False) -> Any:
    from agent.tools.registry import ToolRegistry
    from agent.core.agent import Agent

    llm      = _build_llm(model, extended=extended)
    registry = ToolRegistry.default()

    agent = Agent(
        llm=llm,
        tool_registry=registry,
        max_steps=max_steps,
        use_planner=True,
        verbose=False,
    )
    if step_callback:
        agent._loop.step_callback = step_callback
    return agent


def _serialize_step(step: Any) -> AgentStepInfo:
    return AgentStepInfo(
        step=step.step_number,
        thought=step.thought or "",
        tools_called=[
            {"name": tc.tool_name, "args": tc.tool_input}
            for tc in (step.tool_calls or [])
        ],
        tool_results=[
            {
                "tool":    tr.tool_name,
                "output":  tr.output[:500] if tr.output else "",
                "error":   tr.error,
                "success": tr.success,
            }
            for tr in (step.tool_results or [])
        ],
        is_final=bool(step.final_answer),
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.3.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=round(time.time() - _start_time, 1),
    )


# ---------------------------------------------------------------------------
# Chat UI
# ---------------------------------------------------------------------------

@app.get("/chat-ui", tags=["chat"], include_in_schema=False)
async def chat_ui() -> FileResponse:
    html_path = os.path.join(_TEMPLATES_DIR, "chat.html")
    return FileResponse(html_path, media_type="text/html")


# ---------------------------------------------------------------------------
# /chat — multitask dengan Extended Thinking
# ---------------------------------------------------------------------------

@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or str(uuid.uuid4())
    model      = req.model or "llama-3.3-70b-versatile"

    context = ""
    for msg in (req.history or []):
        prefix = "User" if msg.role == "user" else "Assistant"
        context += f"{prefix}: {msg.content}\n"

    full_prompt = f"{_MULTITASK_SYSTEM}\n\n{context}User: {req.message}\nAssistant:"

    try:
        llm = _build_llm(model, extended=req.extended)
        raw = await llm.generate_text(
            messages=[{"role": "user", "content": full_prompt}],
            max_tokens=4096,
            extended=req.extended,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    thinking: str | None = None
    response_text: str

    if req.extended:
        try:
            parsed        = json.loads(raw)
            thinking      = parsed.get("thinking", "") or None
            response_text = parsed.get("answer", raw)
        except (json.JSONDecodeError, AttributeError):
            response_text = raw
    else:
        response_text = raw

    return ChatResponse(
        session_id=session_id,
        response=response_text.strip(),
        thinking=thinking,
        model_used=model,
        extended=req.extended,
    )


# ---------------------------------------------------------------------------
# /agent/run — agentic blocking
# ---------------------------------------------------------------------------

@app.post("/agent/run", response_model=AgentRunResponse, tags=["agent"])
async def agent_run(req: AgentRunRequest) -> AgentRunResponse:
    model = req.model or "llama-3.3-70b-versatile"
    t0    = time.perf_counter()

    try:
        agent    = _build_agent(model=model, max_steps=req.max_steps, extended=req.extended)
        response = await agent.run(req.task)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    elapsed = time.perf_counter() - t0
    steps   = [_serialize_step(s) for s in (response._state.steps or [])]

    return AgentRunResponse(
        run_id=response.run_id,
        status=response.status.value,
        final_answer=response.final_answer,
        error=response.error,
        steps=steps,
        steps_taken=response.steps_taken,
        duration_seconds=round(elapsed, 2),
        model_used=model,
    )


# ---------------------------------------------------------------------------
# /agent/stream — agentic SSE
# ---------------------------------------------------------------------------

@app.post("/agent/stream", tags=["agent"])
async def agent_stream(req: AgentRunRequest) -> StreamingResponse:
    model = req.model or "llama-3.3-70b-versatile"

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def on_step(step: Any) -> None:
            await queue.put({
                "type":    "step",
                "step":    step.step_number,
                "thought": step.thought or "",
                "tools":   [{"name": tc.tool_name, "args": tc.tool_input} for tc in (step.tool_calls or [])],
                "results": [
                    {
                        "tool":    tr.tool_name,
                        "output":  tr.output[:400] if tr.output else "",
                        "success": tr.success,
                    }
                    for tr in (step.tool_results or [])
                ],
                "is_final": bool(step.final_answer),
            })

        yield f"data: {json.dumps({'type': 'start', 'task': req.task, 'model': model})}\n\n"

        t0 = time.perf_counter()

        async def run_agent():
            try:
                agent    = _build_agent(
                    model=model, max_steps=req.max_steps,
                    step_callback=on_step, extended=req.extended,
                )
                response = await agent.run(req.task)
                elapsed  = round(time.perf_counter() - t0, 2)

                if response.success:
                    await queue.put({
                        "type":        "final",
                        "answer":      response.final_answer or "",
                        "steps_taken": response.steps_taken,
                        "duration":    elapsed,
                    })
                else:
                    await queue.put({"type": "error", "message": response.error or "Agent failed"})
            except Exception as e:
                await queue.put({"type": "error", "message": str(e)})
            finally:
                await queue.put({"type": "__done__"})

        task = asyncio.create_task(run_agent())

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=120.0)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Timeout'})}\n\n"
                break

            if event.get("type") == "__done__":
                break

            yield f"data: {json.dumps(event)}\n\n"

            if event.get("type") in ("final", "error"):
                break

        if not task.done():
            task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Legacy /run endpoints
# ---------------------------------------------------------------------------

def _build_legacy_agent(request: RunRequest) -> Any:
    from agent.tools.registry import ToolRegistry
    from agent.llm.router import LLMRouter
    from agent.core.agent import Agent

    registry = ToolRegistry.default()
    try:
        llm = LLMRouter.from_env()
    except RuntimeError:
        from examples.basic_agent import MockLLM
        llm = MockLLM()

    return Agent(llm=llm, tool_registry=registry, max_steps=request.max_steps,
                 use_planner=True, verbose=False)


@app.post("/run", response_model=RunResponse, tags=["agent-legacy"])
async def run_task(request: RunRequest) -> RunResponse:
    agent    = _build_legacy_agent(request)
    t0       = time.perf_counter()
    response = await agent.run(request.task)
    elapsed  = time.perf_counter() - t0
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


@app.post("/run/stream", tags=["agent-legacy"])
async def run_task_stream(request: RunRequest) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        agent    = _build_legacy_agent(request)
        response = await agent.run(request.task)
        yield f"data: {json.dumps({'type': 'complete' if response.success else 'error', 'run_id': response.run_id, 'final_answer': response.final_answer, 'error': response.error, 'steps': response.steps_taken})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/status/{run_id}", tags=["agent-legacy"])
async def get_status(run_id: str) -> dict[str, Any]:
    if run_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Run ID '{run_id}' not found.")
    return _tasks[run_id]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port",   type=int, default=int(os.environ.get("AGENT_PORT", 8000)))
    parser.add_argument("--host",   type=str, default=os.environ.get("AGENT_HOST", "127.0.0.1"))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run("agent.api:app", host=args.host, port=args.port,
                reload=args.reload, log_level="warning")