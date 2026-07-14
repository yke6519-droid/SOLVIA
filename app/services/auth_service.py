"""JWT 认证服务。"""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt


def _secret_key() -> str:
    """读取 JWT 密钥；生产环境必须显式配置。"""
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret or len(secret) < 32:
        raise RuntimeError("JWT_SECRET_KEY 未配置或长度不足 32 位")
    return secret


def create_access_token(user: Dict[str, Any]) -> tuple[str, int]:
    """创建短时 Access Token。"""
    expires_in = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_SECONDS", "3600"))
    now = datetime.now(timezone.utc)
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


def decode_access_token(token: str) -> Dict[str, Any]:
    """校验并解析 Access Token。"""
    payload = jwt.decode(token, _secret_key(), algorithms=["HS256"])
    if payload.get("type") != "access" or not payload.get("sub"):
        raise jwt.InvalidTokenError("无效的访问令牌")
    return payload
