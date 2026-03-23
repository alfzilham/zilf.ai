from __future__ import annotations
import base64
import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from jose import JWTError, jwt

# ── Config ──
SECRET_KEY   = os.environ.get("SECRET_KEY", "hams-secret-key-change-in-production")
ALGORITHM    = "HS256"
TOKEN_EXPIRE = 60 * 24 * 7   # 7 hari
BCRYPT_ROUNDS = 12

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
# Passlib memblokir password > 72 bytes di layer validasinya sendiri,
# sebelum sampai ke bcrypt — tidak bisa dimatikan dengan parameter apapun.
# Solusi: pakai library bcrypt langsung, tanpa passlib sama sekali.
#
# Pre-hash sha256 → base64 memastikan input ke bcrypt selalu 44 byte,
# sehingga 72-byte limit tidak pernah tercapai apapun panjang password asli.

def _prep(pw: str) -> bytes:
    """sha256(password) → base64 → 44 bytes. Selalu aman untuk bcrypt."""
    digest = hashlib.sha256(pw.encode("utf-8")).digest()
    return base64.b64encode(digest)

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(_prep(password), salt).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_prep(plain), hashed.encode("utf-8"))

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