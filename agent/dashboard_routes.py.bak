"""
=================================================================
  zilf.ai — AI Observability Dashboard Backend
  Tambahkan ke agent/api.py
  PostgreSQL (Railway) + GoPay Payout
=================================================================
"""

# ── TAMBAHKAN IMPORT INI KE BAGIAN ATAS api.py ───────────────
import os
import uuid
import asyncpg
import httpx
from datetime import datetime, date, timedelta
from functools import wraps
from fastapi import Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# ── ENV VARIABLES (set di Railway dashboard) ──────────────────
# DATABASE_URL        = postgresql://user:pass@host:5432/dbname
# DASHBOARD_ACCESS_TOKEN = token-rahasia-anda
# GOPAY_CLIENT_ID     = (isi setelah dapat dari GoPay)
# GOPAY_CLIENT_SECRET = (isi setelah dapat dari GoPay)
# GOPAY_PHONE         = 08xxxxxxxxxx   ← nomor GoPay tujuan Anda
# GOPAY_ENV           = sandbox | production

DATABASE_URL           = os.getenv("DATABASE_URL", "")
DASHBOARD_ACCESS_TOKEN = os.getenv("DASHBOARD_ACCESS_TOKEN", "changeme")
GOPAY_CLIENT_ID        = os.getenv("GOPAY_CLIENT_ID", "")
GOPAY_CLIENT_SECRET    = os.getenv("GOPAY_CLIENT_SECRET", "")
GOPAY_PHONE_DEFAULT    = os.getenv("GOPAY_PHONE", "")
GOPAY_ENV              = os.getenv("GOPAY_ENV", "sandbox")

REVENUE_PER_USER = 20000  # Rp20.000 per pengguna baru

# GoPay Midtrans Endpoint
GOPAY_BASE = (
    "https://api.midtrans.com"
    if GOPAY_ENV == "production"
    else "https://api.sandbox.midtrans.com"
)

# =================================================================
# DB POOL (asyncpg)
# =================================================================
_db_pool = None

async def get_db_pool():
    global _db_pool
    if _db_pool is None:
        if not DATABASE_URL:
            return None
        _db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _db_pool

async def db_fetch(query: str, *args):
    pool = await get_db_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)

async def db_fetchrow(query: str, *args):
    pool = await get_db_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)

async def db_execute(query: str, *args):
    pool = await get_db_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(query, *args)

# =================================================================
# AUTH MIDDLEWARE
# =================================================================
def require_dashboard_token(request: Request):
    token = request.headers.get("X-Dashboard-Token", "")
    if token != DASHBOARD_ACCESS_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid dashboard token")
    return token

# =================================================================
# ROUTER SETUP
# =================================================================
from fastapi import APIRouter
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
payout_router    = APIRouter(prefix="/api/payout",    tags=["payout"])

templates = Jinja2Templates(directory="agent/templates")

# -----------------------------------------------------------------
# GET /api/dashboard/summary
# -----------------------------------------------------------------
@dashboard_router.get("/summary")
async def dashboard_summary(token=Depends(require_dashboard_token)):
    row = await db_fetchrow(f"""
        SELECT
            COUNT(DISTINCT u.id)                                                   AS total_users,
            COUNT(DISTINCT CASE WHEN u.created_at >= CURRENT_DATE
                                THEN u.id END)                                     AS new_users_today,
            COUNT(DISTINCT CASE WHEN u.created_at >= DATE_TRUNC('month', NOW())
                                THEN u.id END)                                     AS new_users_this_month,
            COUNT(DISTINCT CASE WHEN ml.logged_at >= CURRENT_DATE
                                THEN ml.user_id END)                               AS dau,
            COUNT(DISTINCT CASE WHEN ml.logged_at >= DATE_TRUNC('month', NOW())
                                THEN ml.user_id END)                               AS mau,
            ROUND(SUM(ml.cost_idr)::numeric, 2)                                    AS total_cost_idr,
            ROUND(AVG(ml.cost_idr)::numeric, 4)                                    AS avg_cost_per_req_idr,
            ROUND(AVG(ml.latency_ms)::numeric, 2)                                  AS avg_latency_ms,
            SUM(ml.total_tokens)                                                   AS total_tokens_used,
            COUNT(DISTINCT CASE WHEN u.revenue_credited THEN u.id END) * {REVENUE_PER_USER}     AS total_revenue_idr,
            COUNT(DISTINCT CASE WHEN u.created_at >= DATE_TRUNC('month', NOW())
                                AND u.revenue_credited THEN u.id END) * {REVENUE_PER_USER}      AS revenue_this_month_idr
        FROM users u
        LEFT JOIN model_logs ml ON u.id = ml.user_id
        WHERE u.is_active = TRUE
    """)

    series = await db_fetch(f"""
        SELECT
            gs.day::date                                          AS day,
            COUNT(DISTINCT ml.user_id)                           AS dau,
            COUNT(ml.id)                                         AS requests,
            ROUND(AVG(ml.cost_idr)::numeric * COUNT(ml.id), 0)  AS cost_total
        FROM generate_series(NOW() - INTERVAL '6 days', NOW(), '1 day') gs(day)
        LEFT JOIN model_logs ml
               ON ml.logged_at::date = gs.day::date
        GROUP BY gs.day
        ORDER BY gs.day
    """)

    reg_series = await db_fetch("""
        SELECT gs.day::date AS day, COUNT(u.id) AS cnt
        FROM generate_series(NOW() - INTERVAL '6 days', NOW(), '1 day') gs(day)
        LEFT JOIN users u ON u.created_at::date = gs.day::date
        GROUP BY gs.day ORDER BY gs.day
    """)

    lat_by_model = await db_fetch("""
        SELECT model_name, ROUND(AVG(latency_ms)::numeric, 0) AS avg_lat
        FROM model_logs
        WHERE logged_at >= NOW() - INTERVAL '7 days'
        GROUP BY model_name ORDER BY avg_lat DESC
    """)

    mau_series = await db_fetch("""
        SELECT gs.day::date AS day, COUNT(DISTINCT ml.user_id) AS mau
        FROM generate_series(NOW() - INTERVAL '6 days', NOW(), '1 day') gs(day)
        LEFT JOIN model_logs ml
               ON ml.logged_at <= gs.day
              AND ml.logged_at >= DATE_TRUNC('month', gs.day)
        GROUP BY gs.day ORDER BY gs.day
    """)

    days = [str(r["day"]) for r in series]
    return {
        **(dict(row) if row else {}),
        "days_labels":  days,
        "dau_series":   [r["dau"]        for r in series],
        "mau_series":   [r["mau"]        for r in mau_series],
        "reg_series":   [r["cnt"]        for r in reg_series],
        "cost_series":  [float(r["cost_total"] or 0) for r in series],
        "lat_labels":   [r["model_name"] for r in lat_by_model],
        "lat_values":   [float(r["avg_lat"] or 0) for r in lat_by_model],
    }

# -----------------------------------------------------------------
# GET /api/dashboard/users
# -----------------------------------------------------------------
@dashboard_router.get("/users")
async def dashboard_users(token=Depends(require_dashboard_token)):
    rows = await db_fetch("""
        SELECT
            u.id, u.username, u.email, u.plan, u.is_active,
            u.created_at, u.last_login_at, u.revenue_credited,
            COUNT(ml.id)               AS total_requests,
            SUM(ml.total_tokens)       AS total_tokens,
            ROUND(SUM(ml.cost_idr)::numeric, 2) AS total_cost_idr
        FROM users u
        LEFT JOIN model_logs ml ON u.id = ml.user_id
        WHERE u.is_active = TRUE
        GROUP BY u.id
        ORDER BY u.created_at DESC
        LIMIT 500
    """)
    return {"users": [dict(r) for r in rows]}

# -----------------------------------------------------------------
# GET /api/dashboard/revenue
# -----------------------------------------------------------------
@dashboard_router.get("/revenue")
async def dashboard_revenue(token=Depends(require_dashboard_token)):
    row = await db_fetchrow(f"""
        SELECT
            COUNT(*)                                                              AS total_users,
            COUNT(CASE WHEN revenue_credited THEN 1 END)                         AS revenue_users,
            COUNT(CASE WHEN revenue_credited THEN 1 END) * {REVENUE_PER_USER}    AS total_revenue_idr,
            COUNT(CASE WHEN created_at >= CURRENT_DATE THEN 1 END)               AS new_users_today,
            COUNT(CASE WHEN created_at >= DATE_TRUNC('month', NOW()) THEN 1 END) AS new_users_this_month,
            COUNT(CASE WHEN created_at >= DATE_TRUNC('month', NOW())
                       AND revenue_credited THEN 1 END) * {REVENUE_PER_USER}     AS revenue_this_month_idr
        FROM users WHERE is_active = TRUE
    """)
    payout_row = await db_fetchrow("""
        SELECT
            COALESCE(SUM(CASE WHEN gopay_status = 'paid' THEN amount_idr END), 0)    AS paid_idr,
            COALESCE(SUM(CASE WHEN gopay_status != 'paid' THEN amount_idr END), 0)   AS pending_idr
        FROM payout_requests
    """)
    return {**(dict(row) if row else {}), **(dict(payout_row) if payout_row else {})}

# -----------------------------------------------------------------
# GET /api/dashboard/performance
# -----------------------------------------------------------------
@dashboard_router.get("/performance")
async def dashboard_performance(token=Depends(require_dashboard_token)):
    models = await db_fetch("""
        SELECT model_name,
            ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms)::numeric, 0) AS p50,
            ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::numeric, 0) AS p95,
            ROUND(AVG(throughput_tps)::numeric, 2) AS avg_tps
        FROM model_logs
        WHERE logged_at >= NOW() - INTERVAL '30 days'
        GROUP BY model_name
    """)
    daily = await db_fetch("""
        SELECT
            logged_at::date AS day,
            ROUND(COUNT(CASE WHEN error_code IS NOT NULL THEN 1 END)::numeric
                  / NULLIF(COUNT(*),0) * 100, 2) AS error_rate,
            ROUND(AVG(throughput_tps)::numeric, 2) AS tps
        FROM model_logs
        WHERE logged_at >= NOW() - INTERVAL '7 days'
        GROUP BY logged_at::date ORDER BY day
    """)
    return {
        "models":     [r["model_name"] for r in models],
        "p50":        [float(r["p50"] or 0) for r in models],
        "p95":        [float(r["p95"] or 0) for r in models],
        "days":       [str(r["day"]) for r in daily],
        "error_rate": [float(r["error_rate"] or 0) for r in daily],
        "tps":        [float(r["tps"] or 0) for r in daily],
    }

# -----------------------------------------------------------------
# GET /api/dashboard/quality
# -----------------------------------------------------------------
@dashboard_router.get("/quality")
async def dashboard_quality(token=Depends(require_dashboard_token)):
    latest = await db_fetchrow("""
        SELECT AVG(accuracy_score)::float AS acc, AVG(precision_score)::float AS prec,
               AVG(recall_score)::float AS rec,  AVG(f1_score)::float AS f1,
               AVG(confidence_score)::float AS conf, AVG(trust_score)::float AS trust
        FROM model_quality WHERE evaluated_at >= NOW() - INTERVAL '7 days'
    """)
    daily = await db_fetch("""
        SELECT evaluated_at::date AS day,
               AVG(data_drift_score)::float    AS dd,
               AVG(concept_drift_score)::float AS cd,
               AVG(trust_score)::float         AS ts,
               AVG(hallucination_rate)::float  AS hr,
               AVG(toxicity_score)::float      AS tox
        FROM model_quality
        WHERE evaluated_at >= NOW() - INTERVAL '7 days'
        GROUP BY day ORDER BY day
    """)
    r = dict(latest) if latest else {}
    return {
        "radar":        [r.get("acc",0), r.get("prec",0), r.get("rec",0),
                         r.get("f1",0),  r.get("conf",0), r.get("trust",0)],
        "days":         [str(x["day"]) for x in daily],
        "data_drift":   [x["dd"]  or 0 for x in daily],
        "concept_drift":[x["cd"]  or 0 for x in daily],
        "trust":        [x["ts"]  or 0 for x in daily],
        "hallucination":[x["hr"]  or 0 for x in daily],
        "toxicity":     [x["tox"] or 0 for x in daily],
    }

# -----------------------------------------------------------------
# GET /api/dashboard/cost-per-user
# -----------------------------------------------------------------
@dashboard_router.get("/cost-per-user")
async def dashboard_cost_per_user(token=Depends(require_dashboard_token)):
    rows = await db_fetch("""
        SELECT u.email, u.username,
               COUNT(ml.id)                                           AS total_requests,
               COALESCE(SUM(ml.total_tokens), 0)                     AS total_tokens,
               ROUND(COALESCE(SUM(ml.cost_idr),0)::numeric, 2)       AS total_cost_idr,
               ROUND(COALESCE(AVG(ml.cost_idr),0)::numeric, 4)       AS avg_cost_per_request_idr
        FROM users u
        LEFT JOIN model_logs ml ON u.id = ml.user_id
        WHERE u.is_active = TRUE
        GROUP BY u.id, u.email, u.username
        ORDER BY total_cost_idr DESC
        LIMIT 100
    """)
    return {"users": [dict(r) for r in rows]}

# -----------------------------------------------------------------
# GET /api/dashboard/security
# -----------------------------------------------------------------
@dashboard_router.get("/security")
async def dashboard_security(token=Depends(require_dashboard_token)):
    rows = await db_fetch("""
        SELECT sa.detected_at, u.email, sa.owasp_category, sa.owasp_label,
               sa.severity, sa.action_taken, sa.pii_detected
        FROM security_audit sa
        LEFT JOIN users u ON sa.user_id = u.id
        ORDER BY sa.detected_at DESC LIMIT 50
    """)
    return {"logs": [dict(r) for r in rows]}

# -----------------------------------------------------------------
# GET /api/dashboard/pii
# -----------------------------------------------------------------
@dashboard_router.get("/pii")
async def dashboard_pii(token=Depends(require_dashboard_token)):
    today_count = await db_fetchrow("""
        SELECT COUNT(*) AS cnt,
               COUNT(CASE WHEN action_taken='blocked' THEN 1 END) AS blocked
        FROM security_audit
        WHERE pii_detected = TRUE AND detected_at >= CURRENT_DATE
    """)
    type_row = await db_fetchrow("""
        SELECT pii_types[1] AS top_type FROM security_audit
        WHERE pii_detected = TRUE AND detected_at >= NOW() - INTERVAL '7 days'
        AND array_length(pii_types,1) > 0
        GROUP BY pii_types[1] ORDER BY COUNT(*) DESC LIMIT 1
    """)
    daily = await db_fetch("""
        SELECT detected_at::date AS day, COUNT(*) AS cnt
        FROM security_audit
        WHERE pii_detected = TRUE AND detected_at >= NOW() - INTERVAL '7 days'
        GROUP BY day ORDER BY day
    """)
    return {
        "today":    int(today_count["cnt"] or 0) if today_count else 0,
        "blocked":  int(today_count["blocked"] or 0) if today_count else 0,
        "top_type": type_row["top_type"] if type_row else "email",
        "days":     [str(r["day"]) for r in daily],
        "counts":   [int(r["cnt"]) for r in daily],
    }

# =================================================================
# PAYOUT — GoPay (via Midtrans)
# =================================================================
@payout_router.post("/gopay/request")
async def gopay_payout_request(
    payload: dict,
    token=Depends(require_dashboard_token)
):
    phone     = payload.get("gopay_phone", "").strip()
    amount    = int(payload.get("amount_idr", 0))
    user_cnt  = int(payload.get("user_count", 0))

    if amount <= 0 or not phone:
        raise HTTPException(400, "gopay_phone dan amount_idr wajib diisi")

    ref_id = f"zilf-{uuid.uuid4().hex[:12]}"

    if not GOPAY_CLIENT_ID:
        await db_execute("""
            INSERT INTO payout_requests
                (amount_idr, user_count, gopay_phone, gopay_reference_id, gopay_status)
            VALUES ($1, $2, $3, $4, 'pending')
        """, amount, user_cnt, phone, ref_id)
        return {
            "status":       "pending",
            "reference_id": ref_id,
            "message":      "Tercatat. Aktif setelah API GoPay dikonfigurasi.",
            "amount_idr":   amount,
        }

    import base64
    auth = base64.b64encode(f"{GOPAY_CLIENT_ID}:{GOPAY_CLIENT_SECRET}".encode()).decode()
    payload_gopay = {
        "payment_type": "gopay",
        "transaction_details": {"order_id": ref_id, "gross_amount": amount},
        "gopay": {"enable_callback": False, "account_number": phone}
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GOPAY_BASE}/v2/charge",
                json=payload_gopay,
                headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
            )
        data = resp.json()
        status = "success" if data.get("status_code") in ("200","201") else "failed"
        await db_execute("""
            INSERT INTO payout_requests
                (amount_idr, user_count, gopay_phone, gopay_reference_id, gopay_status, gopay_response)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        """, amount, user_cnt, phone, ref_id, status, str(data))
        return {"status": status, "reference_id": ref_id, "gopay_response": data}
    except Exception as e:
        await db_execute("""
            INSERT INTO payout_requests
                (amount_idr, user_count, gopay_phone, gopay_reference_id, gopay_status, notes)
            VALUES ($1, $2, $3, $4, 'failed', $5)
        """, amount, user_cnt, phone, ref_id, str(e))
        raise HTTPException(502, f"GoPay API error: {str(e)}")

# =================================================================
# CARA DAFTARKAN KE api.py
# =================================================================
REGISTER_SNIPPET = '''
# Tambahkan di agent/api.py (setelah app = FastAPI(...))

from agent.dashboard_routes import dashboard_router, payout_router
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

app.include_router(dashboard_router)
app.include_router(payout_router)

templates = Jinja2Templates(directory="agent/templates")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})
'''

if __name__ == "__main__":
    print("Dashboard routes siap. Tambahkan ke api.py sesuai REGISTER_SNIPPET di atas.")