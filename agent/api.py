"""
HTTP API for the Zilf AI — v0.3.0

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
import os
from dotenv import load_dotenv

# Muat variabel lingkungan dari file .env (jalur absolut)
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(env_path)

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.auth import router as auth_router, decode_token, get_user_by_id

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Zilf AI", version="0.3.0")

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
app.include_router(auth_router)

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
    model: str | None = Field(default="zilf-max")  # B3 FIX
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
    model: str | None = Field(default="zilf-max")  # B3 FIX
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

_MULTITASK_SYSTEM = """CRITICAL: Always output blank lines between sections. Never place
a heading immediately after a sentence on the same line. Never run
bullet points together without blank lines separating them from headings.

Kamu adalah ZILF.AI — asisten AI serba bisa yang powerful dan cerdas. You are a helpful AI assistant. Always format your responses using proper Markdown.

Formatting rules:
- Always add a blank line before any heading (##, ###)
- Always add a blank line before any bullet list (-)
- Never place a heading inline after a sentence — always start it on a new line
- Use **bold** for emphasis, not ALL CAPS
- Use bullet points (-) for lists, never run them together in one sentence
- Separate each section with a blank line

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


def _build_llm(model: str = "zilf-max", extended: bool = False):
    """
    Build LLM instance berdasarkan model string dari frontend.

    B3 FIX: Default model = "zilf-max" agar konsisten dengan:
    - Frontend modelSelect default = "zilf-max"
    - _FRONTEND_TO_ZILFMAX["zilf-max"] = ("llama-3.3-70b-versatile", "groq")
    """
    # Gemini models → langsung pakai GoogleLLM
    if model.startswith("gemini-"):
        from agent.llm.google_provider import GoogleLLM
        return GoogleLLM(model=model)

    # Semua model lain → ZilfMax routing
    if extended:
        from agent.llm.zilf_max_thinking import ZilfMaxThinkingLLM
        return ZilfMaxThinkingLLM(model=model)
    else:
        from agent.llm.zilf_max_chat import ZilfMaxChatLLM
        return ZilfMaxChatLLM(model=model)


def _build_agent(
    model: str = "zilf-max",
    max_steps: int = 15,
    step_callback=None,
    extended: bool = False,
):
    """
    Build Agent instance untuk agent mode.

    B3  FIX: Default model = "zilf-max"
    B11 FIX: step_callback passed via Agent.__init__() parameter,
             not via agent._loop.step_callback (private attribute access).
    """
    from agent.llm.zilf_max_agent import ZilfMaxAgentLLM
    from agent.tools.registry import ToolRegistry
    from agent.core.agent import Agent

    llm = ZilfMaxAgentLLM(model=model)
    registry = ToolRegistry.default()

    # B11 FIX: step_callback sebagai parameter resmi
    agent = Agent(
        llm=llm,
        tool_registry=registry,
        max_steps=max_steps,
        use_planner=True,
        verbose=False,
        step_callback=step_callback,  # ✅ Proper parameter, bukan _loop access
    )

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


def _get_current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(auth.split(" ", 1)[1])
    if not payload:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    user = get_user_by_id(int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


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


@app.get("/login", include_in_schema=False)
async def login_page() -> FileResponse:
    return FileResponse(os.path.join(_TEMPLATES_DIR, "login.html"), media_type="text/html")


@app.get("/register", include_in_schema=False)
async def register_page() -> FileResponse:
    return FileResponse(os.path.join(_TEMPLATES_DIR, "register.html"), media_type="text/html")

@app.get("/onboarding/topics", include_in_schema=False)
async def onboarding_topics_page() -> FileResponse:
    return FileResponse(os.path.join(_TEMPLATES_DIR, "onboarding_topics.html"), media_type="text/html")

@app.get("/onboarding/suggestions", include_in_schema=False)
async def onboarding_suggestions_page() -> FileResponse:
    return FileResponse(os.path.join(_TEMPLATES_DIR, "onboarding_suggestions.html"), media_type="text/html")


# ---------------------------------------------------------------------------
# /chat — multitask dengan Extended Thinking
# ---------------------------------------------------------------------------

@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or str(uuid.uuid4())
    model      = req.model or "zilf-max"

    # A2 FIX: Build proper message list instead of manual prompt string.
    # This lets ZilfMaxBase._build_payload() handle system prompt as
    # separate system message (consistent with B20 fix).
    messages: list[dict[str, str]] = []
    for msg in (req.history or []):
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})

    try:
        llm = _build_llm(model, extended=req.extended)

        if req.extended:
            # Extended thinking — use generate_text with system prompt
            raw = await llm.generate_text(
                messages=messages,
                system=_MULTITASK_SYSTEM,
                max_tokens=4096,
            )
        else:
            # Normal chat — use generate_text with system prompt
            raw = await llm.generate_text(
                messages=messages,
                system=_MULTITASK_SYSTEM,
                max_tokens=4096,
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


# ══════════════════════════════════════════════════════════════
# /chat/stream juga perlu A2 FIX yang sama
# ══════════════════════════════════════════════════════════════

@app.post("/chat/stream", tags=["chat"])
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    model = req.model or "zilf-max"

    # A2 FIX: Proper message list
    messages: list[dict[str, str]] = []
    for msg in (req.history or []):
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})

    async def event_stream() -> AsyncIterator[str]:
        try:
            llm = _build_llm(model)
            async for chunk in llm.stream(
                messages=messages,
                system=_MULTITASK_SYSTEM,
            ):
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# /agent/run — agentic blocking
# ---------------------------------------------------------------------------

@app.post("/agent/run", response_model=AgentRunResponse, tags=["agent"])
async def agent_run(req: AgentRunRequest) -> AgentRunResponse:
    model = req.model or "zilf-max"  # B3 FIX
    t0    = time.perf_counter()

    try:
        agent    = _build_agent(
                        model=model,
            max_steps=req.max_steps,
            extended=req.extended,
        )
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
    model = req.model or "zilf-max"  # B3 FIX

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
                    model=model,
                    max_steps=req.max_steps,
                    step_callback=on_step,
                    extended=req.extended,
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
                event = await asyncio.wait_for(queue.get(), timeout=300.0)
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


@app.get("/cli", include_in_schema=False)
async def cli_page() -> FileResponse:
    html_path = os.path.join(_TEMPLATES_DIR, "cli.html")
    return FileResponse(html_path, media_type="text/html")


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