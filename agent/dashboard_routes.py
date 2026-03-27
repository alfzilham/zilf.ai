"""
agent/dashboard_routes.py
Dashboard Observability zilf.ai 2026 — PostgreSQL Version (Clean Rebuild)
"""

import os
import uuid
from datetime import datetime
from typing import Dict, Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

# ====================== CONFIG ======================
DATABASE_URL = os.getenv("DATABASE_URL")
DASHBOARD_ACCESS_TOKEN = os.getenv("DASHBOARD_ACCESS_TOKEN", "").strip()
REVENUE_PER_USER = 20000  # Rp20.000 per pengguna

dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
payout_router = APIRouter(prefix="/api/payout", tags=["payout"])

# ====================== AUTH MIDDLEWARE ======================
async def require_dashboard_token(request: Request):
    if not DASHBOARD_ACCESS_TOKEN:
        return  # development mode tanpa token (Railway tetap wajib)
    
    provided = request.headers.get("X-Dashboard-Token", "").strip()
    if not provided or provided != DASHBOARD_ACCESS_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Token salah atau server tidak merespons."
        )
    return provided

# ====================== DB HELPER ======================
_db_pool = None

async def get_db():
    global _db_pool
    if _db_pool is None:
        if not DATABASE_URL:
            raise HTTPException(503, "Database PostgreSQL belum terkoneksi")
        _db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _db_pool

async def db_fetch(query: str, *args):
    pool = await get_db()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)

async def db_fetchrow(query: str, *args):
    pool = await get_db()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)

async def db_execute(query: str, *args):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(query, *args)

# ====================== ENDPOINTS DASHBOARD ======================

@dashboard_router.get("/summary")
async def dashboard_summary(_=Depends(require_dashboard_token)):
    row = await db_fetchrow("""
        SELECT
            COUNT(*) AS total_users,
            COUNT(CASE WHEN created_at >= CURRENT_DATE THEN 1 END) AS new_users_today,
            COUNT(CASE WHEN created_at >= DATE_TRUNC('month', NOW()) THEN 1 END) AS new_users_this_month,
            COUNT(DISTINCT CASE WHEN logged_at >= CURRENT_DATE THEN user_id END) AS dau,
            COUNT(DISTINCT CASE WHEN logged_at >= DATE_TRUNC('month', NOW()) THEN user_id END) AS mau,
            COALESCE(ROUND(AVG(latency_ms)::numeric, 2), 0) AS avg_latency_ms,
            COALESCE(SUM(total_tokens), 0) AS total_tokens_used,
            COUNT(CASE WHEN revenue_credited THEN 1 END) * $1 AS total_revenue_idr,
            COUNT(CASE WHEN created_at >= DATE_TRUNC('month', NOW()) AND revenue_credited THEN 1 END) * $1 AS revenue_this_month_idr
        FROM users
        LEFT JOIN model_logs ON users.id = model_logs.user_id
        WHERE users.is_active = TRUE
    """, REVENUE_PER_USER)

    return dict(row) if row else {"total_users": 0, "total_revenue_idr": 0}

@dashboard_router.get("/users")
async def dashboard_users(_=Depends(require_dashboard_token)):
    rows = await db_fetch("""
        SELECT id, username, email, plan, created_at, last_login_at, revenue_credited,
               COUNT(model_logs.id) AS total_requests,
               ROUND(COALESCE(SUM(model_logs.cost_idr),0)::numeric, 0) AS total_cost_idr
        FROM users
        LEFT JOIN model_logs ON users.id = model_logs.user_id
        WHERE users.is_active = TRUE
        GROUP BY users.id
        ORDER BY users.created_at DESC
        LIMIT 500
    """)
    return {"users": [dict(r) for r in rows]}

@dashboard_router.get("/revenue")
async def dashboard_revenue(_=Depends(require_dashboard_token)):
    row = await db_fetchrow("""
        SELECT
            COUNT(*) AS total_users,
            COUNT(CASE WHEN revenue_credited THEN 1 END) AS revenue_users,
            COUNT(CASE WHEN revenue_credited THEN 1 END) * $1 AS total_revenue_idr,
            COUNT(CASE WHEN created_at >= CURRENT_DATE THEN 1 END) AS new_users_today,
            COUNT(CASE WHEN created_at >= DATE_TRUNC('month', NOW()) THEN 1 END) AS new_users_this_month,
            COUNT(CASE WHEN created_at >= DATE_TRUNC('month', NOW()) AND revenue_credited THEN 1 END) * $1 AS revenue_this_month_idr
        FROM users WHERE is_active = TRUE
    """, REVENUE_PER_USER)

    payout = await db_fetchrow("""
        SELECT 
            COALESCE(SUM(CASE WHEN status = 'success' THEN amount_idr END), 0) AS paid_idr,
            COALESCE(SUM(CASE WHEN status != 'success' THEN amount_idr END), 0) AS pending_idr
        FROM payout_requests
    """)

    return {**(dict(row) or {}), **(dict(payout) or {})}

# (Endpoint lain seperti /performance, /quality, /cost-per-user, /security, /pii bisa ditambahkan nanti jika tabel sudah ada.
# Untuk sekarang sudah cukup agar login berhasil + revenue & payout jalan.)

# ====================== PAYOUT DOKU RDL → GoPay ======================
from agent.doku_payout import doku_rdl_payout_to_gopay

class GoPayPayoutIn(BaseModel):
    gopay_phone: str

@payout_router.post("/gopay/request")
async def payout_gopay_request(payload: GoPayPayoutIn, _=Depends(require_dashboard_token)):
    phone = payload.gopay_phone.strip()
    if not phone or not phone.startswith("08"):
        raise HTTPException(400, "Nomor GoPay tidak valid")

    # Hitung total
    row = await db_fetchrow("SELECT COUNT(*) AS total_users FROM users WHERE is_active = TRUE")
    total_users = row["total_users"] if row else 0
    amount_idr = total_users * REVENUE_PER_USER

    # Simpan dulu ke DB
    payout_id = f"zilf-{uuid.uuid4().hex[:12]}"
    await db_execute("""
        INSERT INTO payout_requests 
            (id, provider, to_account, amount_idr, total_users, status)
        VALUES ($1, 'doku_rdl', $2, $3, $4, 'pending')
    """, payout_id, phone, amount_idr, total_users)

    # Jalankan DOKU RDL
    result = await doku_rdl_payout_to_gopay(
        amount_idr=amount_idr,
        gopay_phone=phone,
        notes=f"Zilf.ai payout — {total_users} users"
    )

    status = "success" if result.get("status_code") in (200, 201) else "failed"

    await db_execute("UPDATE payout_requests SET status = $1, doku_response = $2 WHERE id = $3",
                     status, json.dumps(result), payout_id)

    return {
        "status": status,
        "reference_id": payout_id,
        "amount_idr": amount_idr,
        "total_users": total_users,
        "doku_response": result
    }