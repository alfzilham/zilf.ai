from __future__ import annotations
import hashlib
import base64
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

# ── Config ──
SECRET_KEY  = os.environ.get("SECRET_KEY", "hams-secret-key-change-in-production")
ALGORITHM   = "HS256"
TOKEN_EXPIRE = 60 * 24 * 7  # 7 hari

# FIX: gunakan bcrypt__truncate_error (bukan truncate_error) agar benar-benar
# diteruskan ke handler bcrypt. truncate_error tanpa prefix diabaikan oleh
# CryptContext → bcrypt default ke truncate_error=True → raise error.
pwd_ctx = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
    bcrypt__truncate_error=False,   # ← FIX: prefix bcrypt__ wajib
)

# ── Database ──
DB_PATH = Path(os.environ.get("HAMS_DB_PATH", "/app/data/hams.db"))

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            username   TEXT    NOT NULL UNIQUE,
            email      TEXT    NOT NULL UNIQUE,
            password   TEXT    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

# ── Password ──
# FIX: ganti _prep (truncate bytes) dengan sha256 pre-hash.
# sha256 → digest 32 bytes → base64 44 bytes, selalu < 72 byte limit bcrypt.
# Tidak ada edge case multi-byte character yang bisa ditruncate di tengah.
def _prep(pw: str) -> bytes:
    """
    Pre-hash password sebelum masuk bcrypt agar tidak pernah melebihi 72 byte.
    sha256(utf-8) → 32 bytes → base64 → 44 bytes (selalu aman untuk bcrypt).
    """
    digest = hashlib.sha256(pw.encode("utf-8")).digest()
    return base64.b64encode(digest)          # bytes, 44 chars, aman

def hash_password(password: str) -> str:
    return pwd_ctx.hash(_prep(password))

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(_prep(plain), hashed)

# ── JWT ──
def create_token(user_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRE)
    return jwt.encode(
        {"sub": str(user_id), "username": username, "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM
    )

def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

# ── User CRUD ──
def create_user(name: str, username: str, email: str, password: str) -> dict:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (name, username, email, password) VALUES (?, ?, ?, ?)",
            (name, username.lower(), email.lower(), hash_password(password))
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.lower(),)
        ).fetchone()
        return dict(row)
    except sqlite3.IntegrityError as e:
        if "username" in str(e):
            raise ValueError("Username sudah digunakan")
        if "email" in str(e):
            raise ValueError("Email sudah terdaftar")
        raise
    finally:
        conn.close()

def get_user_by_email(email: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email.lower(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def update_user_name(user_id: int, name: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE users SET name = ?, updated_at = datetime('now') WHERE id = ?",
        (name, user_id)
    )
    conn.commit()
    conn.close()