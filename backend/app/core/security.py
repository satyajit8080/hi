"""Password hashing and JWT tokens."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# bcrypt is called directly. passlib has been unmaintained since 2020 and its
# bcrypt backend breaks against bcrypt >= 4.1, which is what a fresh install
# resolves to today — the first signup would crash.
ALGORITHM = "HS256"
BCRYPT_ROUNDS = 12


def hash_password(raw: str) -> str:
    # bcrypt silently truncates at 72 bytes; refuse rather than accept a weaker
    # password than the user thinks they set.
    data = raw.encode("utf-8")
    if len(data) > 72:
        raise ValueError("Password must be at most 72 bytes.")
    return bcrypt.hashpw(data, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def _encode(payload: dict) -> str:
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(user_id: str, tier: str) -> str:
    now = datetime.now(timezone.utc)
    return _encode(
        {
            "sub": str(user_id),
            "tier": tier,
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=settings.access_token_minutes)).timestamp()),
        }
    )


def create_refresh_token(user_id: str) -> tuple[str, uuid.UUID, datetime]:
    now = datetime.now(timezone.utc)
    jti = uuid.uuid4()
    expires = now + timedelta(days=settings.refresh_token_days)
    token = _encode(
        {
            "sub": str(user_id),
            "jti": str(jti),
            "type": "refresh",
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        }
    )
    return token, jti, expires


def decode_token(token: str, expected_type: str) -> dict:
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"expected {expected_type} token")
    return payload
