"""JWT issuance, validation and refresh-cookie helpers."""
import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from uuid import uuid4

import jwt

from backend.app.config import settings


REFRESH_COOKIE_NAME = "solar_agent_refresh_token"


def _secret_key() -> str:
    """读取 JWT 密钥；生产环境必须显式配置。"""
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret or len(secret) < 32:
        raise RuntimeError("JWT_SECRET_KEY 未配置或长度不足 32 位")
    return secret


def utcnow() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def _positive_seconds(value: int, setting_name: str) -> int:
    seconds = int(value)
    if seconds <= 0:
        raise RuntimeError(f"{setting_name} 必须大于 0")
    return seconds


def create_access_token(user: Dict[str, Any]) -> tuple[str, int]:
    """创建短时 Access Token。"""
    expires_in = _positive_seconds(
        settings.jwt_access_token_expire_seconds,
        "JWT_ACCESS_TOKEN_EXPIRE_SECONDS",
    )
    now = utcnow()
    # 封装令牌内的数据
    payload = {
        "sub": str(user["user_id"]),
        "username": user["username"],
        "role": user.get("role", "user"),
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "type": "access",
    }
    # 编码令牌并返回
    return jwt.encode(payload, _secret_key(), algorithm="HS256"), expires_in


def create_refresh_token(
    user: Dict[str, Any],
    *,
    expires_at: datetime | None = None,
) -> tuple[str, str, datetime]:
    """Create a refresh token bound to one server-side refresh session.

    ``expires_at`` is carried across rotation, so this is an absolute session
    expiry rather than a sliding seven-day window.
    """
    now = utcnow()
    if expires_at is None:
        refresh_lifetime = _positive_seconds(
            settings.jwt_refresh_token_expire_seconds,
            "JWT_REFRESH_TOKEN_EXPIRE_SECONDS",
        )
        expires_at = now + timedelta(seconds=refresh_lifetime)
    elif expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)

    if expires_at <= now:
        raise ValueError("Refresh Token 已过期，无法轮换")

    token_id = uuid4().hex
    payload = {
        "sub": str(user["user_id"]),
        "username": user["username"],
        "role": user.get("role", "user"),
        "jti": token_id,
        "iat": now,
        "exp": expires_at,
        "type": "refresh",
    }
    token = jwt.encode(payload, _secret_key(), algorithm="HS256")
    return token, token_id, expires_at


def decode_access_token(token: str) -> Dict[str, Any]:
    """校验并解析 Access Token。"""
    payload = jwt.decode(token, _secret_key(), algorithms=["HS256"])
    if payload.get("type") != "access" or not payload.get("sub"):
        raise jwt.InvalidTokenError("无效的访问令牌")
    return payload


def decode_refresh_token(token: str) -> Dict[str, Any]:
    """Validate and decode a refresh token without accepting access tokens."""
    payload = jwt.decode(token, _secret_key(), algorithms=["HS256"])
    if (
        payload.get("type") != "refresh"
        or not payload.get("sub")
        or not payload.get("jti")
    ):
        raise jwt.InvalidTokenError("无效的刷新令牌")
    return payload


def hash_refresh_token(token: str) -> str:
    """Store only a SHA-256 digest; never persist the raw refresh token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_cookie_max_age(expires_at: datetime) -> int:
    """Calculate the remaining absolute refresh-session lifetime in seconds."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return max(0, int((expires_at - utcnow()).total_seconds()))
