"""Shared FastAPI dependencies."""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db import session_dep


@dataclass(slots=True)
class CurrentUser:
    id: str
    email: str
    tier: str


async def _resolve(token: str, session: AsyncSession) -> CurrentUser | None:
    try:
        payload = decode_token(token, "access")
    except jwt.PyJWTError:
        return None
    row = await session.execute(
        text("SELECT id, email, tier, is_active FROM users WHERE id = :id"),
        {"id": payload["sub"]},
    )
    r = row.first()
    if r is None or not r.is_active:
        return None
    return CurrentUser(id=str(r.id), email=r.email, tier=r.tier)


async def optional_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(session_dep),
) -> CurrentUser | None:
    """Anonymous access is first-class: the public track record needs no login."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return await _resolve(authorization.split(" ", 1)[1], session)


async def current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(session_dep),
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    user = await _resolve(authorization.split(" ", 1)[1], session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has expired. Sign in again.")
    return user


async def pro_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if user.tier != "pro":
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "This is a Pro feature. Upgrade to unlock real-time signals, alerts and full history.",
        )
    return user


def tier_of(user: CurrentUser | None) -> str:
    return user.tier if user else "free"
