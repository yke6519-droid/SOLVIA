"""FastAPI 当前用户依赖。"""
from typing import Any, Dict

import jwt
from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.services.auth_service import decode_access_token
from backend.app.errors import AppError, ErrorCode

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any]:
    """从 Bearer Token 获取当前用户。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            ErrorCode.AUTH_REQUIRED,
            "缺少有效的登录凭证",
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
        return {
            "user_id": int(payload["sub"]),
            "username": payload.get("username", ""),
            "role": payload.get("role", "user"),
        }
    except jwt.ExpiredSignatureError as exc:
        raise AppError(
            ErrorCode.AUTH_TOKEN_EXPIRED,
            "登录凭证已过期",
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except RuntimeError as exc:
        raise AppError(
            ErrorCode.AUTH_CONFIG_ERROR,
            "认证服务配置错误",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc
    except (ValueError, jwt.PyJWTError) as exc:
        raise AppError(
            ErrorCode.AUTH_TOKEN_INVALID,
            "登录凭证无效",
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
