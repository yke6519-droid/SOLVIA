"""FastAPI 当前用户依赖。"""
from typing import Any, Dict

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.services.auth_service import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any]:
    """从 Bearer Token 获取当前用户。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少有效的登录凭证")
    try:
        payload = decode_access_token(credentials.credentials)
        return {
            "user_id": int(payload["sub"]),
            "username": payload.get("username", ""),
            "role": payload.get("role", "user"),
        }
    except (ValueError, jwt.PyJWTError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录凭证无效或已过期") from exc
