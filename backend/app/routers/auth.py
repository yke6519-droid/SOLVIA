"""
auth.py - 认证路由
==================
对应 Spring 的 @RestController + @RequestMapping("/api/auth")。

认证策略:
  - 短期 Access Token 用于业务接口鉴权。
  - 可撤销、可轮换的 Refresh Token 仅用于续期。
"""
import logging
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Cookie, Response, status

from backend.app.config import settings
from backend.app.schemas.user import (
    RegisterRequest,
    LoginRequest,
    UserResponse,
    AuthResponse,
    LogoutResponse,
)
from backend.app.services.auth_service import (
    REFRESH_COOKIE_NAME,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_refresh_token,
    refresh_cookie_max_age,
)
from backend.app.services.refresh_token_service import (
    create_refresh_session,
    revoke_refresh_session,
    rotate_refresh_session,
)
from backend.app.services.user_service import get_user_by_id, login_user, register_user
from backend.app.errors import AppError, ErrorCode

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _to_user_response(user: dict, message: str) -> UserResponse:
    return UserResponse(
        user_id=user["user_id"],
        username=user["username"],
        role=user["role"],
        display_name=user.get("display_name"),
        message=message,
    )


def _set_refresh_cookie(response: Response, refresh_token: str, expires_at: datetime) -> None:
    """Expose refresh credentials only through a scoped HttpOnly cookie."""
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=refresh_cookie_max_age(expires_at),
        httponly=True,
        secure=settings.jwt_refresh_cookie_secure,
        samesite="lax",
        path="/api/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        httponly=True,
        secure=settings.jwt_refresh_cookie_secure,
        samesite="lax",
        path="/api/auth",
    )


def _refresh_auth_error(
    response: Response,
    code: ErrorCode,
    message: str,
    *,
    status_code: int,
) -> AppError:
    """Clear a bad refresh cookie even when FastAPI renders an error response."""
    _clear_refresh_cookie(response)
    cookie_header = response.headers.get("set-cookie")
    return AppError(
        code,
        message,
        status_code=status_code,
        headers={"Set-Cookie": cookie_header} if cookie_header else None,
    )


def _issue_auth_response(
    user: dict,
    response: Response,
    *,
    refresh_expires_at: datetime | None = None,
    success_message: str = "登录成功",
) -> AuthResponse:
    """Issue a short access token and persist a rotating refresh session."""
    access_token, expires_in = create_access_token(user)
    refresh_token, token_id, expires_at = create_refresh_token(
        user,
        expires_at=refresh_expires_at,
    )
    create_refresh_session(
        token_id=token_id,
        token_hash=hash_refresh_token(refresh_token),
        user_id=int(user["user_id"]),
        expires_at=expires_at,
    )
    _set_refresh_cookie(response, refresh_token, expires_at)
    return AuthResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=_to_user_response(user, success_message),
    )


@router.post("/register", response_model=UserResponse)
async def register(req: RegisterRequest):
    """注册新用户。"""
    try:
        user = register_user(req.username, req.password, req.display_name or "")
        return UserResponse(
            user_id=user["user_id"],
            username=user["username"],
            role=user["role"],
            display_name=user["display_name"],
            message="注册成功",
        )
    except ValueError as exc:
        raise AppError(
            ErrorCode.REQUEST_VALIDATION_FAILED,
            str(exc),
            status_code=422,
        ) from exc


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, response: Response):
    """用户登录。"""
    try:
        user = login_user(req.username, req.password)
        return _issue_auth_response(user, response)
    except ValueError as exc:
        raise AppError(ErrorCode.AUTH_LOGIN_FAILED, str(exc), status_code=401) from exc
    except RuntimeError as exc:
        raise AppError(
            ErrorCode.AUTH_CONFIG_ERROR,
            "认证服务配置错误",
            status_code=500,
        ) from exc


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
):
    """Rotate an HttpOnly refresh token and return a new short Access Token."""
    if not refresh_token:
        raise _refresh_auth_error(
            response,
            ErrorCode.AUTH_REFRESH_TOKEN_MISSING,
            "登录会话不存在，请重新登录",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        # 解析refreshToken，验证其有效性和过期时间
        payload = decode_refresh_token(refresh_token)
    except jwt.ExpiredSignatureError as exc:
        raise _refresh_auth_error(
            response,
            ErrorCode.AUTH_REFRESH_TOKEN_EXPIRED,
            "登录会话已过期，请重新登录",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc
    except (ValueError, jwt.PyJWTError) as exc:
        raise _refresh_auth_error(
            response,
            ErrorCode.AUTH_REFRESH_TOKEN_INVALID,
            "刷新登录状态失败，请重新登录",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc
    # 验证refreshToken是否在数据库中存在
    user = get_user_by_id(int(payload["sub"]))
    if not user:
        raise _refresh_auth_error(
            response,
            ErrorCode.AUTH_REFRESH_TOKEN_INVALID,
            "登录用户不存在，请重新登录",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if int(user.get("status", 1)) == 0:
        raise _refresh_auth_error(
            response,
            ErrorCode.AUTH_ACCOUNT_DISABLED,
            "该账号已被禁用，请联系管理员",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    expires_at = datetime.fromtimestamp(int(payload["exp"]), timezone.utc)
    new_refresh_token, new_token_id, _ = create_refresh_token(
        user,
        expires_at=expires_at,
    )

    rotate_refresh_session(
        token_id=str(payload["jti"]),
        token_hash=hash_refresh_token(refresh_token),
        user_id=int(user["user_id"]),
        new_token_id=new_token_id,
        new_token_hash=hash_refresh_token(new_refresh_token),
        expires_at=expires_at,
    )

    access_token, expires_in = create_access_token(user)
    
    _set_refresh_cookie(response, new_refresh_token, expires_at)
    return AuthResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=_to_user_response(user, "登录状态已续期"),
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
):
    """Revoke the current refresh session and always clear the browser cookie."""
    if refresh_token:
        try:
            payload = decode_refresh_token(refresh_token)
            revoke_refresh_session(
                str(payload["jti"]),
                hash_refresh_token(refresh_token),
            )
        except jwt.PyJWTError:
            # An expired or malformed token is already unusable; clear it below.
            pass
    _clear_refresh_cookie(response)
    return LogoutResponse()
