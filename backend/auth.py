"""Local role accounts for the volunteer and coordinator portals."""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
TOKEN_HOURS = 12


def auth_required() -> bool:
    return os.getenv("GIM_AUTH_REQUIRED", "0") == "1"


def _db():
    from backend.sessions import _connection
    return _connection()


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000).hex()


def seed_account(username: str, role: str, password: str) -> None:
    if role not in {"student", "coordinator"} or len(password) < 12:
        raise ValueError("invalid_account")
    salt = secrets.token_bytes(24)
    with _db() as con:
        con.execute("""INSERT INTO portal_accounts(username,role,password_salt,password_hash)
            VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET
            role=excluded.role,password_salt=excluded.password_salt,
            password_hash=excluded.password_hash""",
            (username, role, salt.hex(), _password_hash(password, salt)))


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


def require_account(authorization: str | None, role: str | None = None) -> dict:
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if not token or len(token) > 200:
        raise HTTPException(status_code=401, detail="login_required")
    digest = hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
    with _db() as con:
        row = con.execute("""SELECT a.username,a.role,t.expires_at FROM portal_tokens t
            JOIN portal_accounts a ON a.username=t.username WHERE t.token_hash=?""",
            (digest,)).fetchone()
    if not row or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="login_required")
    if role and row["role"] != role:
        raise HTTPException(status_code=403, detail="wrong_role")
    return {"username": row["username"], "role": row["role"]}


@router.post("/auth/login")
def login(data: LoginInput):
    with _db() as con:
        row = con.execute("SELECT * FROM portal_accounts WHERE username=?",
                          (data.username,)).fetchone()
        if row:
            actual = _password_hash(data.password, bytes.fromhex(row["password_salt"]))
            valid = secrets.compare_digest(actual, row["password_hash"])
        else:
            _password_hash(data.password, b"\0" * 24)
            valid = False
        if not valid:
            raise HTTPException(status_code=401, detail="invalid_credentials")
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
        con.execute("INSERT INTO portal_tokens(token_hash,username,expires_at) VALUES(?,?,?)",
                    (hashlib.sha256(token.encode()).hexdigest(), row["username"], expires.isoformat()))
    return {"access_token": token, "token_type": "bearer", "role": row["role"],
            "username": row["username"], "expires_at": expires.isoformat()}


@router.get("/auth/me")
def me(authorization: str | None = Header(default=None)):
    account = require_account(authorization)
    if account["role"] == "student":
        with _db() as con:
            rows = con.execute("""SELECT s.id,s.state,s.active_run_id,s.created_at
                FROM sessions s JOIN session_owners o ON o.session_id=s.id
                WHERE o.username=? ORDER BY s.created_at DESC""", (account["username"],)).fetchall()
        account["sessions"] = [dict(row) for row in rows]
    return account


@router.post("/auth/logout", status_code=204)
def logout(authorization: str | None = Header(default=None)):
    require_account(authorization)
    token = authorization.removeprefix("Bearer ")
    with _db() as con:
        con.execute("DELETE FROM portal_tokens WHERE token_hash=?",
                    (hashlib.sha256(token.encode()).hexdigest(),))
