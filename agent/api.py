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
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, Response, RedirectResponse
from fastapi import UploadFile, File, Form
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
_DATA_DIR      = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(_DATA_DIR, exist_ok=True)
_UPLOADS_DIR   = os.path.join(_DATA_DIR, "uploads")
os.makedirs(_UPLOADS_DIR, exist_ok=True)
_FEEDBACK_DB   = os.path.join(_DATA_DIR, "feedback.db")
_feedback_streams: dict[str, list[asyncio.Queue]] = {}

def _init_feedback_db():
    import sqlite3
    conn = sqlite3.connect(_FEEDBACK_DB)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,user_id INTEGER,email TEXT NOT NULL,tags TEXT DEFAULT '',resolved INTEGER DEFAULT 0,created_at TEXT,updated_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,thread_id TEXT NOT NULL,sender TEXT NOT NULL,message TEXT NOT NULL,rating INTEGER,category TEXT,created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY,message_id TEXT NOT NULL,path TEXT NOT NULL,mime TEXT,size INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS admin_users(id TEXT PRIMARY KEY,name TEXT NOT NULL,pass_hash TEXT NOT NULL,created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS admin_sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires_at TEXT NOT NULL,created_at TEXT)")
    conn.commit()
    conn.close()

_init_feedback_db()

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
app.mount("/static/feedback_files", StaticFiles(directory=_UPLOADS_DIR), name="feedback_files")
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

class FeedbackMessageIn(BaseModel):
    email: str
    message: str
    rating: int | None = None
    category: str | None = None
    thread_id: str | None = None


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

def _feedback_db_conn():
    import sqlite3
    return sqlite3.connect(_FEEDBACK_DB)

def _pbkdf2_hash(password: str, salt: bytes, iterations: int = 200_000) -> str:
    import base64
    import hashlib
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(dk).decode()}"

def _verify_pbkdf2(password: str, stored: str) -> bool:
    import base64
    import hashlib
    import hmac
    try:
        algo, it_s, salt_b64, dk_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(it_s)
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(dk_b64.encode())
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False

def _ensure_bootstrap_admin():
    name = os.environ.get("FEEDBACK_ADMIN_BOOTSTRAP_NAME", "").strip()
    password = os.environ.get("FEEDBACK_ADMIN_BOOTSTRAP_PASSWORD", "")
    if not name or not password:
        return
    conn = _feedback_db_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(1) FROM admin_users")
    if (c.fetchone() or [0])[0] > 0:
        conn.close()
        return
    import uuid
    salt = os.urandom(16)
    now = datetime.now(timezone.utc).isoformat()
    c.execute(
        "INSERT INTO admin_users(id,name,pass_hash,created_at) VALUES(?,?,?,?)",
        (str(uuid.uuid4()), name, _pbkdf2_hash(password, salt), now),
    )
    conn.commit()
    conn.close()

_ensure_bootstrap_admin()

def _require_admin(request: Request) -> dict:
    token = request.cookies.get("fb_admin", "")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    conn = _feedback_db_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, expires_at FROM admin_sessions WHERE token=?", (token,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id, expires_at = row
    if expires_at <= datetime.now(timezone.utc).isoformat():
        c.execute("DELETE FROM admin_sessions WHERE token=?", (token,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=401, detail="Not authenticated")
    c.execute("SELECT id,name FROM admin_users WHERE id=?", (user_id,))
    u = c.fetchone()
    conn.close()
    if not u:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"id": u[0], "name": u[1]}

def _feedback_broadcast(thread_id: str, event: dict):
    qs = _feedback_streams.get(thread_id, [])
    for q in qs:
        try:
            q.put_nowait(event)
        except Exception:
            pass

@app.get("/feedback", include_in_schema=False)
async def feedback_page(request: Request):
    return FileResponse(os.path.join(_TEMPLATES_DIR, "feedback.html"), media_type="text/html")

@app.get("/admin/feedback", include_in_schema=False)
async def feedback_admin_page(request: Request):
    try:
        _require_admin(request)
        return FileResponse(os.path.join(_TEMPLATES_DIR, "feedback_admin.html"), media_type="text/html")
    except HTTPException:
        return RedirectResponse(url="/admin/feedback/login", status_code=302)

@app.get("/admin/feedback/login", include_in_schema=False)
async def feedback_admin_login_page() -> FileResponse:
    return FileResponse(os.path.join(_TEMPLATES_DIR, "feedback_admin_login.html"), media_type="text/html")

@app.post("/api/admin/feedback/login", tags=["feedback"])
async def feedback_admin_login(request: Request, name: str = Form(...), password: str = Form(...)):
    conn = _feedback_db_conn()
    c = conn.cursor()
    c.execute("SELECT id, pass_hash FROM admin_users WHERE name=?", (name.strip(),))
    row = c.fetchone()
    if not row or not _verify_pbkdf2(password, row[1]):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    import uuid
    now = datetime.now(timezone.utc)
    exp = (now + timedelta(days=30)).isoformat()
    tok = str(uuid.uuid4())
    c.execute("INSERT INTO admin_sessions(token,user_id,expires_at,created_at) VALUES(?,?,?,?)", (tok, row[0], exp, now.isoformat()))
    conn.commit()
    conn.close()
    resp = Response(content=json.dumps({"ok": True}), media_type="application/json")
    resp.set_cookie("fb_admin", tok, httponly=True, samesite="lax", secure=(request.url.scheme == "https"), path="/")
    return resp

@app.post("/api/admin/feedback/logout", tags=["feedback"])
async def feedback_admin_logout(request: Request):
    token = request.cookies.get("fb_admin", "")
    if token:
        conn = _feedback_db_conn()
        c = conn.cursor()
        c.execute("DELETE FROM admin_sessions WHERE token=?", (token,))
        conn.commit()
        conn.close()
    resp = Response(content=json.dumps({"ok": True}), media_type="application/json")
    resp.delete_cookie("fb_admin", path="/")
    return resp

@app.get("/api/admin/feedback/threads", tags=["feedback"])
async def admin_feedback_threads(request: Request, q: str | None = None):
    _require_admin(request)
    conn = _feedback_db_conn()
    c = conn.cursor()
    if q:
        like = f"%{q}%"
        c.execute("SELECT id,email,tags,resolved,created_at,updated_at FROM threads WHERE email LIKE ? OR tags LIKE ? ORDER BY updated_at DESC", (like, like))
    else:
        c.execute("SELECT id,email,tags,resolved,created_at,updated_at FROM threads ORDER BY updated_at DESC")
    rows = c.fetchall()
    conn.close()
    return [{"id":r[0],"email":r[1],"tags":r[2],"resolved":bool(r[3]),"created_at":r[4],"updated_at":r[5]} for r in rows]

@app.get("/api/admin/feedback/messages", tags=["feedback"])
async def admin_feedback_messages(request: Request, thread_id: str):
    _require_admin(request)
    conn = _feedback_db_conn()
    c = conn.cursor()
    c.execute("SELECT id,sender,message,rating,category,created_at FROM messages WHERE thread_id=? ORDER BY created_at ASC", (thread_id,))
    rows = c.fetchall()
    conn.close()
    return [{"id":r[0],"sender":r[1],"message":r[2],"rating":r[3],"category":r[4],"created_at":r[5]} for r in rows]

@app.post("/api/admin/feedback/messages", tags=["feedback"])
async def admin_feedback_post_message(request: Request, thread_id: str = Form(...), message: str = Form(...)):
    admin = _require_admin(request)
    if not message.strip():
        raise HTTPException(status_code=400, detail="Message required")
    import uuid
    now = datetime.now(timezone.utc).isoformat()
    conn = _feedback_db_conn()
    c = conn.cursor()
    mid = str(uuid.uuid4())
    c.execute("INSERT INTO messages(id,thread_id,sender,message,rating,category,created_at) VALUES(?,?,?,?,?,?,?)",
              (mid, thread_id, "admin", message, None, None, now))
    c.execute("UPDATE threads SET updated_at=? WHERE id=?", (now, thread_id))
    conn.commit()
    conn.close()
    _feedback_broadcast(thread_id, {"type":"message","sender":"admin","message":message,"created_at":now})
    return {"ok": True, "message_id": mid, "admin": admin.get("name")}

@app.patch("/api/admin/feedback/threads/{thread_id}/resolve", tags=["feedback"])
async def admin_feedback_resolve(request: Request, thread_id: str, resolved: bool = True):
    _require_admin(request)
    conn = _feedback_db_conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("UPDATE threads SET resolved=?, updated_at=? WHERE id=?", (1 if resolved else 0, now, thread_id))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.patch("/api/admin/feedback/threads/{thread_id}/tags", tags=["feedback"])
async def admin_feedback_tags(request: Request, thread_id: str, tags: str):
    _require_admin(request)
    conn = _feedback_db_conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("UPDATE threads SET tags=?, updated_at=? WHERE id=?", (tags, now, thread_id))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/admin/feedback/stream", tags=["feedback"])
async def admin_feedback_stream(request: Request, thread_id: str):
    _require_admin(request)
    async def es():
        q: asyncio.Queue = asyncio.Queue()
        _feedback_streams.setdefault(thread_id, []).append(q)
        try:
            while True:
                ev = await q.get()
                yield f"data: {json.dumps(ev)}\n\n"
        except asyncio.CancelledError:
            ...
        finally:
            try:
                _feedback_streams[thread_id].remove(q)
            except Exception:
                ...
    return StreamingResponse(es(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.get("/api/admin/feedback/export.csv", tags=["feedback"])
async def admin_feedback_export(request: Request):
    _require_admin(request)
    import csv, io
    conn = _feedback_db_conn()
    c = conn.cursor()
    c.execute("SELECT t.id,t.email,t.tags,t.resolved,m.created_at,m.sender,m.message,m.rating,m.category FROM messages m JOIN threads t ON m.thread_id=t.id ORDER BY t.updated_at DESC,m.created_at ASC")
    rows = c.fetchall()
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["thread_id","email","tags","resolved","created_at","sender","message","rating","category"])
    for r in rows:
        w.writerow(r)
    return Response(content=buf.getvalue(), media_type="text/csv")

@app.post("/api/feedback/messages", tags=["feedback"])
async def feedback_post(request: Request, data: FeedbackMessageIn):
    user = _get_current_user(request)
    if not data.email or not data.message:
        raise HTTPException(status_code=400, detail="Email and message required")
    import uuid
    now = datetime.now(timezone.utc).isoformat()
    conn = _feedback_db_conn()
    c = conn.cursor()
    tid = data.thread_id or str(uuid.uuid4())
    c.execute("SELECT id FROM threads WHERE id=?", (tid,))
    if c.fetchone() is None:
        c.execute("INSERT INTO threads(id,user_id,email,tags,resolved,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (tid, user.get("id"), data.email, "", 0, now, now))
    mid = str(uuid.uuid4())
    c.execute("INSERT INTO messages(id,thread_id,sender,message,rating,category,created_at) VALUES(?,?,?,?,?,?,?)",
              (mid, tid, "user", data.message, data.rating, data.category, now))
    c.execute("UPDATE threads SET updated_at=? WHERE id=?", (now, tid))
    conn.commit()
    conn.close()
    _feedback_broadcast(tid, {"type":"message","sender":"user","message":data.message,"created_at":now,"rating":data.rating,"category":data.category})
    return {"ok": True, "thread_id": tid, "message_id": mid}

@app.get("/api/feedback/threads", tags=["feedback"])
async def feedback_threads(request: Request, q: str | None = None):
    _get_current_user(request)
    conn = _feedback_db_conn()
    c = conn.cursor()
    if q:
        like = f"%{q}%"
        c.execute("SELECT id,email,tags,resolved,created_at,updated_at FROM threads WHERE email LIKE ? OR tags LIKE ? ORDER BY updated_at DESC", (like, like))
    else:
        c.execute("SELECT id,email,tags,resolved,created_at,updated_at FROM threads ORDER BY updated_at DESC")
    rows = c.fetchall()
    conn.close()
    return [{"id":r[0],"email":r[1],"tags":r[2],"resolved":bool(r[3]),"created_at":r[4],"updated_at":r[5]} for r in rows]

@app.get("/api/feedback/messages", tags=["feedback"])
async def feedback_messages(request: Request, thread_id: str):
    _get_current_user(request)
    conn = _feedback_db_conn()
    c = conn.cursor()
    c.execute("SELECT id,sender,message,rating,category,created_at FROM messages WHERE thread_id=? ORDER BY created_at ASC", (thread_id,))
    rows = c.fetchall()
    conn.close()
    return [{"id":r[0],"sender":r[1],"message":r[2],"rating":r[3],"category":r[4],"created_at":r[5]} for r in rows]

@app.patch("/api/feedback/threads/{thread_id}/resolve", tags=["feedback"])
async def feedback_resolve(request: Request, thread_id: str, resolved: bool = True):
    _get_current_user(request)
    conn = _feedback_db_conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("UPDATE threads SET resolved=?, updated_at=? WHERE id=?", (1 if resolved else 0, now, thread_id))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.patch("/api/feedback/threads/{thread_id}/tags", tags=["feedback"])
async def feedback_tags(request: Request, thread_id: str, tags: str):
    _get_current_user(request)
    conn = _feedback_db_conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("UPDATE threads SET tags=?, updated_at=? WHERE id=?", (tags, now, thread_id))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/feedback/stream", tags=["feedback"])
async def feedback_stream(request: Request, thread_id: str, token: str | None = None):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not decode_token(token):
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        if not decode_token(auth.split(" ", 1)[1]):
            raise HTTPException(status_code=401, detail="Invalid token")
    async def es():
        q: asyncio.Queue = asyncio.Queue()
        _feedback_streams.setdefault(thread_id, []).append(q)
        try:
            while True:
                ev = await q.get()
                yield f"data: {json.dumps(ev)}\n\n"
        except asyncio.CancelledError:
            ...
        finally:
            try:
                _feedback_streams[thread_id].remove(q)
            except Exception:
                ...
    return StreamingResponse(es(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.get("/api/feedback/export.csv", tags=["feedback"])
async def feedback_export(request: Request):
    _get_current_user(request)
    import csv, io
    conn = _feedback_db_conn()
    c = conn.cursor()
    c.execute("SELECT t.id,t.email,t.tags,t.resolved,m.created_at,m.sender,m.message,m.rating,m.category FROM messages m JOIN threads t ON m.thread_id=t.id ORDER BY t.updated_at DESC,m.created_at ASC")
    rows = c.fetchall()
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["thread_id","email","tags","resolved","created_at","sender","message","rating","category"])
    for r in rows:
        w.writerow(r)
    return Response(content=buf.getvalue(), media_type="text/csv")

@app.post("/api/feedback/upload", tags=["feedback"])
async def feedback_upload(request: Request, thread_id: str = Form(...), files: list[UploadFile] = File(...)):
    _get_current_user(request)
    upload_dir = os.path.join(_DATA_DIR, "uploads", thread_id)
    os.makedirs(upload_dir, exist_ok=True)
    saved = []
    for f in files:
        ext = os.path.splitext(f.filename)[1]
        name = str(uuid.uuid4()) + ext
        path = os.path.join(upload_dir, name)
        with open(path, "wb") as w:
            w.write(await f.read())
        saved.append({"name": f.filename, "path": f"/static/feedback_files/{thread_id}/{name}"})
    return {"files": saved}


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

async def _team_refine_agent_answer(
    task: str,
    draft_answer: str,
    steps: list[Any] | None,
    *,
    lang: str = "id",
    review_timeout_s: float = 6.0,
    edit_timeout_s: float = 8.0,
) -> str:
    if not draft_answer.strip():
        return draft_answer

    from agent.llm.zilf_max_chat import ZilfMaxChatLLM

    max_chars = 4500
    ctx_parts: list[str] = []
    if steps:
        for s in steps:
            try:
                if getattr(s, "thought", None):
                    thought = (s.thought or "").strip()
                    if thought:
                        ctx_parts.append(f"Thought: {thought[:220]}")
                for tc in (getattr(s, "tool_calls", None) or []):
                    name = getattr(tc, "tool_name", "") or ""
                    args = getattr(tc, "tool_input", None)
                    ctx_parts.append(f"Tool: {name} args={json.dumps(args)[:260]}")
                for tr in (getattr(s, "tool_results", None) or []):
                    name = getattr(tr, "tool_name", "") or ""
                    out = (getattr(tr, "output", "") or "").strip()
                    if out:
                        ctx_parts.append(f"Result: {name} -> {out[:320]}")
            except Exception:
                continue
            if sum(len(x) for x in ctx_parts) > max_chars:
                break

    evidence = "\n".join(ctx_parts)[:max_chars]

    if lang.lower().startswith("id"):
        reviewer_system = (
            "Kamu adalah reviewer senior. Beri feedback kritis dan konkret, "
            "fokus pada: keakuratan, struktur, kelengkapan langkah, keamanan, dan kejelasan."
        )
        editor_system = (
            "Kamu adalah editor final yang menyusun jawaban paling optimal dan profesional. "
            "Output harus Markdown rapi, actionable, tidak bertele-tele, dan konsisten."
        )
        review_prompt = (
            f"TUGAS:\n{task}\n\n"
            f"BUKTI/KONTEKS (ringkas):\n{evidence}\n\n"
            f"DRAFT JAWABAN:\n{draft_answer}\n\n"
            "Buat:\n"
            "1) Daftar masalah/risiko (bullet)\n"
            "2) Saran perbaikan (bullet)\n"
            "3) Versi jawaban yang sudah ditulis ulang (Markdown)\n"
        )
        merge_prompt = (
            f"TUGAS:\n{task}\n\n"
            f"DRAFT AWAL:\n{draft_answer}\n\n"
            "MASUKAN TIM:\n"
            "{REVIEWS}\n\n"
            "Tulis jawaban final terbaik dalam Markdown. Jangan tampilkan proses berpikir."
        )
    else:
        reviewer_system = (
            "You are a senior reviewer. Give concrete, critical feedback focusing on accuracy, "
            "structure, completeness, safety, and clarity."
        )
        editor_system = (
            "You are the final editor producing the most optimal, professional answer. "
            "Output must be clean Markdown, actionable, and concise."
        )
        review_prompt = (
            f"TASK:\n{task}\n\n"
            f"EVIDENCE/CONTEXT (brief):\n{evidence}\n\n"
            f"DRAFT ANSWER:\n{draft_answer}\n\n"
            "Provide:\n"
            "1) Issues/Risks (bullets)\n"
            "2) Improvements (bullets)\n"
            "3) Rewritten answer (Markdown)\n"
        )
        merge_prompt = (
            f"TASK:\n{task}\n\n"
            f"ORIGINAL DRAFT:\n{draft_answer}\n\n"
            "TEAM FEEDBACK:\n"
            "{REVIEWS}\n\n"
            "Write the best final answer in Markdown. Do not include chain-of-thought."
        )

    reviewer_models = [
        "nvidia/nemotron-super-3",
        "nvidia/deepseek-v3.2",
        "nvidia/qwen-3.5",
    ]
    editor_model = "llama-3.3-70b-versatile"

    async def run_review(model_key: str) -> str | None:
        try:
            llm = ZilfMaxChatLLM(model=model_key)
            return await asyncio.wait_for(
                llm.generate_text(
                    messages=[{"role": "user", "content": review_prompt}],
                    system=reviewer_system,
                ),
                timeout=review_timeout_s,
            )
        except Exception:
            return None

    reviews_raw = await asyncio.gather(*(run_review(m) for m in reviewer_models))
    reviews = "\n\n---\n\n".join([r for r in reviews_raw if r and r.strip()])
    if not reviews.strip():
        return draft_answer

    try:
        llm = ZilfMaxChatLLM(model=editor_model)
        refined = await asyncio.wait_for(
            llm.generate_text(
                messages=[{"role": "user", "content": merge_prompt.replace("{REVIEWS}", reviews)}],
                system=editor_system,
            ),
            timeout=edit_timeout_s,
        )
        return refined.strip() or draft_answer
    except Exception:
        return draft_answer


@app.post("/agent/run", response_model=AgentRunResponse, tags=["agent"])
async def agent_run(req: AgentRunRequest) -> AgentRunResponse:
    """
    Hierarchical + Sequential orchestration:
    - Planner (Groq) → plan
    - Workers (router) → execute subtasks
    - Editor (Groq) → merge/refine
    """
    from agent.multi_agent.message_bus import MessageBus
    from agent.multi_agent.supervisor import SupervisorAgent
    from agent.multi_agent.worker import WorkerAgent
    from agent.tools.registry import ToolRegistry
    from agent.llm.zilf_max_chat import ZilfMaxChatLLM
    from agent.llm.zilf_max_agent import ZilfMaxAgentLLM

    t0 = time.perf_counter()
    planner_llm = ZilfMaxChatLLM(model="llama-3.3-70b-versatile")
    worker_llm  = ZilfMaxAgentLLM(model=req.model or "zilf-max")

    bus = MessageBus()
    registry = ToolRegistry.default()
    supervisor = SupervisorAgent(llm=planner_llm, bus=bus, timeout_per_subtask=90.0)
    supervisor.add_worker(WorkerAgent("coder_1", "coder", worker_llm, registry, bus, max_steps=req.max_steps))
    supervisor.add_worker(WorkerAgent("reviewer_1", "reviewer", worker_llm, registry, bus, max_steps=max(8, req.max_steps - 2)))
    supervisor.add_worker(WorkerAgent("documenter_1", "documenter", worker_llm, registry, bus, max_steps=6))

    try:
        result = await supervisor.run(req.task)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    draft_md = result.to_markdown()
    refined = await _team_refine_agent_answer(
        req.task, draft_md, steps=None, lang="id",
        review_timeout_s=8.0, edit_timeout_s=10.0
    )

    elapsed = time.perf_counter() - t0
    return AgentRunResponse(
        run_id=result.run_id,
        status="success" if result.success else "partial",
        final_answer=refined,
        error=None if result.success else ("Some subtasks failed" if result.failed_subtasks else None),
        steps=[],
        steps_taken=result.total_steps,
        duration_seconds=round(elapsed, 2),
        model_used=req.model or "zilf-max",
    )


# ---------------------------------------------------------------------------
# /agent/stream — agentic SSE
# ---------------------------------------------------------------------------

@app.post("/agent/stream", tags=["agent"])
async def agent_stream(req: AgentRunRequest) -> StreamingResponse:
    model = req.model or "zilf-max"

    async def event_stream() -> AsyncIterator[str]:
        from agent.multi_agent.message_bus import MessageBus
        from agent.multi_agent.supervisor import SupervisorAgent
        from agent.multi_agent.worker import WorkerAgent
        from agent.tools.registry import ToolRegistry
        from agent.llm.zilf_max_chat import ZilfMaxChatLLM
        from agent.llm.zilf_max_agent import ZilfMaxAgentLLM

        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def progress_cb(ev: dict) -> None:
            await queue.put(ev)

        planner_llm = ZilfMaxChatLLM(model="llama-3.3-70b-versatile")
        worker_llm  = ZilfMaxAgentLLM(model=model)

        bus = MessageBus()
        registry = ToolRegistry.default()
        supervisor = SupervisorAgent(llm=planner_llm, bus=bus, timeout_per_subtask=90.0, progress_cb=progress_cb)
        supervisor.add_worker(WorkerAgent("coder_1", "coder", worker_llm, registry, bus, max_steps=req.max_steps))
        supervisor.add_worker(WorkerAgent("reviewer_1", "reviewer", worker_llm, registry, bus, max_steps=max(8, req.max_steps - 2)))
        supervisor.add_worker(WorkerAgent("documenter_1", "documenter", worker_llm, registry, bus, max_steps=6))

        yield f"data: {json.dumps({'type': 'start', 'task': req.task, 'model': model})}\n\n"

        async def run_team():
            try:
                result = await supervisor.run(req.task)
                draft_md = result.to_markdown()
                refined = await _team_refine_agent_answer(
                    req.task, draft_md, steps=None, lang="id",
                    review_timeout_s=3.0, edit_timeout_s=4.0
                )
                await queue.put({
                    "type": "final",
                    "answer": refined,
                    "steps_taken": result.total_steps,
                    "duration": 0,
                })
            except Exception as e:
                await queue.put({"type": "error", "message": str(e)})
            finally:
                await queue.put({"type": "__done__"})

        task = asyncio.create_task(run_team())

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
