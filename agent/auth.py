"""
HAMS.AI — Authentication Module (v2)
=====================================
- FastAPI Router with all auth endpoints
- SQLite user CRUD
- bcrypt password hashing (sha256 pre-hash)
- JWT token management
- Google OAuth scaffold
- Rate limiting support
"""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field, validator

# ═══════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════
SECRET_KEY    = os.environ.get("SECRET_KEY", "hams-secret-key-change-in-production")
ALGORITHM     = "HS256"
TOKEN_EXPIRE  = 60 * 24 * 7   # 7 days in minutes
BCRYPT_ROUNDS = 12

# Google OAuth (scaffold — set via environment)
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
_RAILWAY_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
_BASE_URL = (
    os.environ.get("APP_BASE_URL")
    or (f"https://{_RAILWAY_URL}" if _RAILWAY_URL else "")
    or "http://localhost:8000"
)
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI",
    f"{_BASE_URL}/auth/google/callback"
)

# Rate limiting: max attempts per IP
RATE_LIMIT_MAX    = 10      # max attempts
RATE_LIMIT_WINDOW = 300     # 5 minutes in seconds

# ═══════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════
DB_PATH = Path(os.environ.get("HAMS_DB_PATH", "/app/data/hams.db"))


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize database tables. Safe to call multiple times."""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            username        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            email           TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            password        TEXT    NOT NULL,
            google_id       TEXT    DEFAULT NULL,
            avatar_url      TEXT    DEFAULT NULL,
            is_active       INTEGER NOT NULL DEFAULT 1,
            reset_token     TEXT    DEFAULT NULL,
            reset_expires   TEXT    DEFAULT NULL,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id)
    """)
    conn.commit()
    conn.close()


# Auto-initialize on import
init_db()


# ═══════════════════════════════════════════════
# PASSWORD HASHING
# ═══════════════════════════════════════════════
def _prep(pw: str) -> bytes:
    """sha256(password) → base64 → 44 bytes. Always safe for bcrypt."""
    digest = hashlib.sha256(pw.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(_prep(password), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prep(plain), hashed.encode("utf-8"))
    except Exception:
        return False


# ═══════════════════════════════════════════════
# JWT TOKEN
# ═══════════════════════════════════════════════
def create_token(user_id: int, username: str, email: str = "") -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRE)
    return jwt.encode(
        {
            "sub": str(user_id),
            "username": username,
            "email": email,
            "exp": expire,
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


# ═══════════════════════════════════════════════
# USER CRUD
# ═══════════════════════════════════════════════
def create_user(
    name: str,
    username: str,
    email: str,
    password: str,
    google_id: Optional[str] = None,
) -> dict:
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO users (name, username, email, password, google_id)
               VALUES (?, ?, ?, ?, ?)""",
            (name, username.lower(), email.lower(), hash_password(password), google_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.lower(),)
        ).fetchone()
        return dict(row)
    except sqlite3.IntegrityError as e:
        err = str(e).lower()
        if "username" in err:
            raise ValueError("Username already taken")
        if "email" in err:
            raise ValueError("Email already registered")
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


def get_user_by_username(username: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username.lower(),)
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


def get_user_by_google_id(google_id: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE google_id = ?", (google_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_name(user_id: int, name: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE users SET name = ?, updated_at = datetime('now') WHERE id = ?",
        (name, user_id),
    )
    conn.commit()
    conn.close()


def update_user_google_id(user_id: int, google_id: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE users SET google_id = ?, updated_at = datetime('now') WHERE id = ?",
        (google_id, user_id),
    )
    conn.commit()
    conn.close()

def update_user_avatar(user_id: int, avatar_url: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE users SET avatar_url = ?, updated_at = datetime('now') WHERE id = ?",
        (avatar_url, user_id),
    )
    conn.commit()
    conn.close()


def set_reset_token(user_id: int, token: str, expires_minutes: int = 30) -> None:
    expires = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    conn = get_db()
    conn.execute(
        """UPDATE users SET reset_token = ?, reset_expires = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (token, expires.isoformat(), user_id),
    )
    conn.commit()
    conn.close()


def verify_reset_token(token: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE reset_token = ?", (token,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    user = dict(row)
    if user.get("reset_expires"):
        expires = datetime.fromisoformat(user["reset_expires"])
        if datetime.now(timezone.utc) > expires:
            return None
    return user


def update_password(user_id: int, new_password: str) -> None:
    conn = get_db()
    conn.execute(
        """UPDATE users SET password = ?, reset_token = NULL, reset_expires = NULL,
           updated_at = datetime('now') WHERE id = ?""",
        (hash_password(new_password), user_id),
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════
# RATE LIMITING (in-memory, per IP)
# ═══════════════════════════════════════════════
_rate_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(ip: str) -> bool:
    """Returns True if request is allowed, False if rate limited."""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # Clean old entries
    _rate_store[ip] = [t for t in _rate_store[ip] if t > window_start]

    if len(_rate_store[ip]) >= RATE_LIMIT_MAX:
        return False

    _rate_store[ip].append(now)
    return True


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ═══════════════════════════════════════════════
# PYDANTIC MODELS (Request/Response)
# ═══════════════════════════════════════════════
class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    username: str = Field(..., min_length=3, max_length=30)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

    @validator("username")
    def validate_username(cls, v):
        if not v.replace("_", "").isalnum():
            raise ValueError("Username can only contain letters, numbers, and underscore")
        return v.lower()

    @validator("password")
    def validate_password(cls, v):
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one number")
        if not any(not c.isalnum() for c in v):
            raise ValueError("Password must contain at least one special character")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)

    @validator("new_password")
    def validate_password(cls, v):
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one number")
        if not any(not c.isalnum() for c in v):
            raise ValueError("Password must contain at least one special character")
        return v


class UpdateProfileRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)


# ═══════════════════════════════════════════════
# FASTAPI ROUTER
# ═══════════════════════════════════════════════
router = APIRouter(prefix="/auth", tags=["Authentication"])


def _user_response(user: dict, token: str) -> dict:
    """Standard auth response format matching frontend expectations."""
    return {
        "token": token,
        "user_id": user["id"],
        "name": user["name"],
        "username": user["username"],
        "email": user["email"],
        "avatar_url": user.get("avatar_url", ""),
    }


# ── POST /auth/register ──
@router.post("/register")
async def register(req: RegisterRequest, request: Request):
    # Rate limit
    ip = _get_client_ip(request)
    if not _check_rate_limit(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please try again in a few minutes.",
        )

    try:
        user = create_user(
            name=req.name,
            username=req.username,
            email=req.email,
            password=req.password,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    token = create_token(user["id"], user["username"], user["email"])
    return JSONResponse(_user_response(user, token), status_code=201)


# ── POST /auth/login ──
@router.post("/login")
async def login(req: LoginRequest, request: Request):
    # Rate limit
    ip = _get_client_ip(request)
    if not _check_rate_limit(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Too many attempts. Please try again in a few minutes.",
        )

    user = get_user_by_email(req.email)
    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Please contact support.",
        )

    token = create_token(user["id"], user["username"], user["email"])
    return JSONResponse(_user_response(user, token))


# ── GET /auth/check-username ──
@router.get("/check-username")
async def check_username(username: str):
    """Real-time username availability check for register form."""
    if not username or len(username) < 3:
        return {"available": False, "message": "Username must be at least 3 characters"}

    if not username.replace("_", "").isalnum():
        return {"available": False, "message": "Only letters, numbers, and underscore"}

    existing = get_user_by_username(username)
    if existing:
        return {"available": False, "message": "Username already taken"}

    return {"available": True, "message": "Username is available"}


# ── POST /auth/forgot-password ──
@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest, request: Request):
    """Generate password reset token. In production, send via email."""
    ip = _get_client_ip(request)
    if not _check_rate_limit(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please try again later.",
        )

    user = get_user_by_email(req.email)

    # Always return success to prevent email enumeration
    if not user:
        return {"message": "If an account with that email exists, a reset link has been sent."}

    # Generate reset token
    import secrets
    reset_token = secrets.token_urlsafe(32)
    set_reset_token(user["id"], reset_token, expires_minutes=30)

    # TODO: In production, send email with reset link:
    # reset_url = f"{BASE_URL}/reset-password?token={reset_token}"
    # send_email(user["email"], "Password Reset", reset_url)

    # For development, log the token
    print(f"[AUTH] Password reset token for {user['email']}: {reset_token}")

    return {"message": "If an account with that email exists, a reset link has been sent."}


# ── POST /auth/reset-password ──
@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    """Reset password using token from forgot-password flow."""
    user = verify_reset_token(req.token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    update_password(user["id"], req.new_password)
    return {"message": "Password has been reset successfully. You can now login."}


# ── GET /auth/me ──
@router.get("/me")
async def get_me(request: Request):
    """Get current user profile from JWT token."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = get_user_by_id(int(payload["sub"]))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return {
        "user_id": user["id"],
        "name": user["name"],
        "username": user["username"],
        "email": user["email"],
        "avatar_url": user.get("avatar_url"),
        "created_at": user["created_at"],
    }


# ── PUT /auth/profile ──
@router.put("/profile")
async def update_profile(req: UpdateProfileRequest, request: Request):
    """Update user profile (name)."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user_id = int(payload["sub"])
    update_user_name(user_id, req.name)

    user = get_user_by_id(user_id)
    return {
        "message": "Profile updated",
        "name": user["name"],
    }


# ── GET /auth/google ──
@router.get("/google")
async def google_login_redirect():
    """Redirect to Google OAuth consent screen."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables.",
            )

    import urllib.parse

    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    })

    return RedirectResponse(
        url=f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
    )


# ── GET /auth/google/callback ──
@router.get("/google/callback")
async def google_callback(code: str = "", error: str = ""):
    """Handle Google OAuth callback."""
    if error:
        return RedirectResponse(url=f"/login?error={error}")

    if not code:
        return RedirectResponse(url="/login?error=no_code")

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return RedirectResponse(url="/login?error=oauth_not_configured")

    try:
        import httpx

        # Exchange code for tokens
        async with httpx.AsyncClient() as client:
            token_res = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )

            if token_res.status_code != 200:
                return RedirectResponse(url="/login?error=token_exchange_failed")

            tokens = token_res.json()
            access_token = tokens.get("access_token")

            # Get user info
            userinfo_res = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if userinfo_res.status_code != 200:
                return RedirectResponse(url="/login?error=userinfo_failed")

            google_user = userinfo_res.json()

        google_id = google_user.get("id")
        email = google_user.get("email", "")
        name = google_user.get("name", "")
        picture = google_user.get("picture", "")

        if not google_id or not email:
            return RedirectResponse(url="/login?error=missing_google_data")

        # Check if user exists by google_id
        user = get_user_by_google_id(google_id)

        if not user:
            # Check if user exists by email
            user = get_user_by_email(email)

            if user:
                # Link Google account to existing user
                update_user_google_id(user["id"], google_id)
            else:
                # Create new user
                import secrets

                username = email.split("@")[0].lower()
                # Ensure unique username
                base_username = username
                counter = 1
                while get_user_by_username(username):
                    username = f"{base_username}{counter}"
                    counter += 1

                # Generate random password (user won't need it for Google login)
                random_pw = secrets.token_urlsafe(24) + "A1!"

                user = create_user(
                    name=name,
                    username=username,
                    email=email,
                    password=random_pw,
                    google_id=google_id,
                )

        # Simpan/update avatar dari Google (setelah semua kondisi di atas)
        if picture:
            update_user_avatar(user["id"], picture)
            user = get_user_by_id(user["id"])  # refresh data

        # Generate JWT
        token = create_token(user["id"], user["username"], user["email"])

        # Redirect to frontend with token
        # Frontend will extract token from URL and store in localStorage
        import urllib.parse

        import json
        user_data = urllib.parse.quote(json.dumps({
            "user_id": user["id"],
            "name": user["name"],
            "username": user["username"],
            "email": user["email"],
            "avatar_url": user.get("avatar_url") or "",
        }))

        return RedirectResponse(
            url=f"/chat-ui?token={token}&user={user_data}"
        )

    except ImportError:
        return RedirectResponse(url="/login?error=httpx_not_installed")
    except Exception as e:
        print(f"[AUTH] Google OAuth error: {e}")
        return RedirectResponse(url="/login?error=oauth_failed")


# ═══════════════════════════════════════════════
# HELPER — Extract token from request
# ═══════════════════════════════════════════════
def _extract_token(request: Request) -> Optional[str]:
    """Extract JWT from Authorization header or query param."""
    # Check Authorization header
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    # Check query param (fallback)
    token = request.query_params.get("token")
    if token:
        return token

    return None


# ═══════════════════════════════════════════════
# MIDDLEWARE HELPER — Auth dependency for protected routes
# ═══════════════════════════════════════════════
async def require_auth(request: Request) -> dict:
    """FastAPI dependency to require authentication.

    Usage:
        @app.get("/protected")
        async def protected_route(user: dict = Depends(require_auth)):
            return {"hello": user["username"]}
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_user_by_id(int(payload["sub"]))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    return user