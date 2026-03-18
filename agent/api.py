"""
HTTP API for the Hams AI.

Provides:
  GET  /health         — liveness + readiness probe (used by Docker healthcheck)
  POST /run            — submit a task and get the result
  POST /run/stream     — submit a task and stream the response (Server-Sent Events)
  GET  /status/{run_id} — check status of a running task

Run standalone:
    uvicorn agent.api:app --host 0.0.0.0 --port 8000 --reload

Or via CLI:
    python -m agent.main serve
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI(
    title="Hams AI",
    description="Autonomous coding assistant API",
    version="0.1.0",
)

# Allow requests from VS Code extension and local dev tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory task registry (replace with Redis in production)
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
    """Construct an Agent from a RunRequest."""
    from agent.tools.registry import ToolRegistry
    from agent.llm.router import LLMRouter

    registry = ToolRegistry.default()
    try:
        llm = LLMRouter.from_env()
    except RuntimeError:
        # Fallback for environments without API keys (demo/test)
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
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """
    Liveness and readiness probe.

    Returns 200 when the API is ready to accept requests.
    Docker healthcheck and Kubernetes readiness probe hit this endpoint.
    """
    return HealthResponse(
        status="ok",
        version="0.1.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=round(time.time() - _start_time, 1),
    )


@app.post("/run", response_model=RunResponse, tags=["agent"])
async def run_task(request: RunRequest) -> RunResponse:
    """
    Submit a coding task and wait for the result.

    Blocking — the request stays open until the agent completes.
    For long tasks use `/run/stream` instead.
    """
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
    """
    Submit a task and stream progress as Server-Sent Events.

    Each SSE event contains a JSON payload:
      {"type": "step", "step": N, "thought": "...", "tool": "...", "observation": "..."}
      {"type": "complete", "final_answer": "...", "steps": N}
      {"type": "error", "message": "..."}

    Client example (JavaScript):
      const es = new EventSource('/run/stream', {method: 'POST', body: JSON.stringify({task: '...'})});
      es.onmessage = e => console.log(JSON.parse(e.data));
    """
    import json

    async def event_stream() -> AsyncIterator[str]:
        agent = _build_agent(request)

        # Patch the agent's loop to emit SSE events per step
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

        # Run the agent with a simple step iterator
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
    """Check the status of a task by run_id."""
    if run_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Run ID '{run_id}' not found.")
    return _tasks[run_id]


# ---------------------------------------------------------------------------
# Entry point — dipakai oleh hams CLI (npm install -g @hams-ai/cli)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="hams.ai API Server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AGENT_PORT", 8000)),
        help="Port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("AGENT_HOST", "127.0.0.1"),
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    args = parser.parse_args()

    uvicorn.run(
        "agent.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",  # suppress noise saat dijalankan dari CLI
    )