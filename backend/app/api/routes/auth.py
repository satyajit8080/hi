"""Authentication."""
from __future__ import annotations

from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db import session_dep
from app.services.subscriptions import tier_summary

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


async def _issue(session: AsyncSession, user_id: str, email: str, tier: str) -> dict:
    access = create_access_token(user_id, tier)
    refresh, jti, expires = create_refresh_token(user_id)
    await session.execute(
        text(
            """
            INSERT INTO refresh_tokens (jti, user_id, expires_at)
            VALUES (:jti, :uid, :exp)
            """
        ),
        {"jti": str(jti), "uid": user_id, "exp": expires},
    )
    await session.commit()
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": {"id": user_id, "email": email, "tier": tier},
        "tier": tier_summary(tier),
    }


@router.post("/signup", status_code=201)
async def signup(body: Credentials, session: AsyncSession = Depends(session_dep)):
    existing = await session.execute(
        text("SELECT 1 FROM users WHERE email_lower = lower(:email)"), {"email": body.email}
    )
    if existing.first():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists.")

    row = await session.execute(
        text(
            """
            INSERT INTO users (email, password_hash, tier)
            VALUES (:email, :pw, 'free')
            RETURNING id, email, tier
            """
        ),
        {"email": body.email, "pw": hash_password(body.password)},
    )
    r = row.one()
    await session.execute(
        text(
            """
            INSERT INTO subscriptions (user_id, provider, status)
            VALUES (:uid, 'none', 'inactive')
            ON CONFLICT (user_id) DO NOTHING
            """
        ),
        {"uid": str(r.id)},
    )
    return await _issue(session, str(r.id), r.email, r.tier)


@router.post("/login")
async def login(body: Credentials, session: AsyncSession = Depends(session_dep)):
    row = await session.execute(
        text("SELECT id, email, tier, password_hash, is_active FROM users WHERE email_lower = lower(:email)"),
        {"email": body.email},
    )
    r = row.first()
    if r is None or not verify_password(body.password, r.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That email and password do not match.")
    if not r.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled.")

    await session.execute(
        text("UPDATE users SET last_login_at = now() WHERE id = :id"), {"id": str(r.id)}
    )
    return await _issue(session, str(r.id), r.email, r.tier)


@router.post("/refresh")
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(session_dep)):
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That refresh token is not valid.")

    row = await session.execute(
        text(
            """
            SELECT r.jti, r.revoked_at, r.expires_at, u.id, u.email, u.tier
            FROM refresh_tokens r JOIN users u ON u.id = r.user_id
            WHERE r.jti = :jti
            """
        ),
        {"jti": payload["jti"]},
    )
    r = row.first()
    if r is None or r.revoked_at is not None or r.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That session has ended. Sign in again.")

    # Rotate: a refresh token is single use.
    await session.execute(
        text("UPDATE refresh_tokens SET revoked_at = now() WHERE jti = :jti"), {"jti": payload["jti"]}
    )
    return await _issue(session, str(r.id), r.email, r.tier)


@router.post("/logout", status_code=204)
async def logout(
    body: RefreshRequest,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(session_dep),
):
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except jwt.PyJWTError:
        return
    await session.execute(
        text("UPDATE refresh_tokens SET revoked_at = now() WHERE jti = :jti AND user_id = :uid"),
        {"jti": payload["jti"], "uid": user.id},
    )
    await session.commit()


@router.get("/me")
async def me(user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(session_dep)):
    sub = await session.execute(
        text(
            """
            SELECT provider, status, price_usd, current_period_end, cancel_at_period_end
            FROM subscriptions WHERE user_id = :uid
            """
        ),
        {"uid": user.id},
    )
    s = sub.first()
    return {
        "id": user.id,
        "email": user.email,
        "tier": user.tier,
        "entitlements": tier_summary(user.tier),
        "subscription": (
            {
                "provider": s.provider,
                "status": s.status,
                "price_usd": float(s.price_usd),
                "current_period_end": s.current_period_end.isoformat() if s.current_period_end else None,
                "cancel_at_period_end": s.cancel_at_period_end,
            }
            if s
            else None
        ),
    }
