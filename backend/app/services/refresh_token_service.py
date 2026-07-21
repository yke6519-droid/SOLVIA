"""Persistence and rotation of revocable refresh-token sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.database import get_engine
from backend.app.errors import AppError, ErrorCode


def _db_time(value: datetime) -> datetime:
    """MySQL DATETIME values are stored as UTC-naive timestamps."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _utc_now_db() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_mapping(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


def create_refresh_session(
    *,
    token_id: str,
    token_hash: str,
    user_id: int,
    expires_at: datetime,
) -> None:
    """Persist a newly issued refresh token without storing its raw value."""
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO auth_refresh_session "
                    "(token_id, token_hash, user_id, expires_at) "
                    "VALUES (:token_id, :token_hash, :user_id, :expires_at)"
                ),
                {
                    "token_id": token_id,
                    "token_hash": token_hash,
                    "user_id": user_id,
                    "expires_at": _db_time(expires_at),
                },
            )
    except SQLAlchemyError as exc:
        raise AppError(
            ErrorCode.DB_UNAVAILABLE,
            "登录会话暂时无法保存，请稍后重试",
            status_code=503,
            retryable=True,
        ) from exc


def rotate_refresh_session(
    *,
    token_id: str,
    token_hash: str,
    user_id: int,
    new_token_id: str,
    new_token_hash: str,
    expires_at: datetime,
) -> None:
    """Atomically revoke the presented token and persist its replacement."""
    now = _utc_now_db()
    try:
        with get_engine().begin() as conn:
            row = conn.execute(
                text(
                    "SELECT user_id, expires_at, revoked_at FROM auth_refresh_session "
                    "WHERE token_id = :token_id AND token_hash = :token_hash FOR UPDATE"
                ),
                {"token_id": token_id, "token_hash": token_hash},
            ).fetchone()
            if row is None:
                raise AppError(
                    ErrorCode.AUTH_REFRESH_TOKEN_INVALID,
                    "刷新登录状态失败，请重新登录",
                    status_code=401,
                )

            session = _as_mapping(row)
            if int(session["user_id"]) != int(user_id):
                raise AppError(
                    ErrorCode.AUTH_REFRESH_TOKEN_INVALID,
                    "刷新登录状态失败，请重新登录",
                    status_code=401,
                )
            if session["revoked_at"] is not None:
                raise AppError(
                    ErrorCode.AUTH_REFRESH_TOKEN_REVOKED,
                    "登录会话已失效，请重新登录",
                    status_code=401,
                )
            if session["expires_at"] <= now:
                raise AppError(
                    ErrorCode.AUTH_REFRESH_TOKEN_EXPIRED,
                    "登录会话已过期，请重新登录",
                    status_code=401,
                )

            updated = conn.execute(
                text(
                    "UPDATE auth_refresh_session "
                    "SET revoked_at = :now, last_used_at = :now, "
                    "replaced_by_token_id = :new_token_id "
                    "WHERE token_id = :token_id AND token_hash = :token_hash "
                    "AND revoked_at IS NULL"
                ),
                {
                    "now": now,
                    "new_token_id": new_token_id,
                    "token_id": token_id,
                    "token_hash": token_hash,
                },
            )
            if updated.rowcount != 1:
                raise AppError(
                    ErrorCode.AUTH_REFRESH_TOKEN_REVOKED,
                    "登录会话已失效，请重新登录",
                    status_code=401,
                )

            conn.execute(
                text(
                    "INSERT INTO auth_refresh_session "
                    "(token_id, token_hash, user_id, expires_at) "
                    "VALUES (:token_id, :token_hash, :user_id, :expires_at)"
                ),
                {
                    "token_id": new_token_id,
                    "token_hash": new_token_hash,
                    "user_id": user_id,
                    "expires_at": _db_time(expires_at),
                },
            )
    except AppError:
        raise
    except SQLAlchemyError as exc:
        raise AppError(
            ErrorCode.DB_UNAVAILABLE,
            "登录会话暂时无法刷新，请稍后重试",
            status_code=503,
            retryable=True,
        ) from exc


def revoke_refresh_session(token_id: str, token_hash: str) -> None:
    """Revoke one refresh session; logout remains idempotent."""
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "UPDATE auth_refresh_session SET revoked_at = COALESCE(revoked_at, :now) "
                    "WHERE token_id = :token_id AND token_hash = :token_hash"
                ),
                {"now": _utc_now_db(), "token_id": token_id, "token_hash": token_hash},
            )
    except SQLAlchemyError as exc:
        raise AppError(
            ErrorCode.DB_UNAVAILABLE,
            "退出登录失败，请稍后重试",
            status_code=503,
            retryable=True,
        ) from exc
