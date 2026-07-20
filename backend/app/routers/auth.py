"""
auth.py - 认证路由
==================
对应 Spring 的 @RestController + @RequestMapping("/api/auth")。

当前阶段:
  - 注册/登录返回 user_id，前端存储后请求时携带
  - 暂不做 JWT Token，前后端分离时加
"""
import logging
from fastapi import APIRouter

from backend.app.schemas.user import RegisterRequest, LoginRequest, UserResponse, AuthResponse
from backend.app.services.auth_service import create_access_token
from backend.app.services.user_service import register_user, login_user
from backend.app.errors import AppError, ErrorCode

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


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
async def login(req: LoginRequest):
    """用户登录。"""
    try:
        # 用户进行登录
        user = login_user(req.username, req.password)
        # 登录成功后，通过 create_access_token 发放JWT令牌
        token, expires_in = create_access_token(user)
        return AuthResponse(
            access_token=token,
            expires_in=expires_in,
            user=UserResponse(
                user_id=user["user_id"],
                username=user["username"],
                role=user["role"],
                display_name=user["display_name"],
                message="登录成功",
            ),
        )
    except ValueError as exc:
        raise AppError(ErrorCode.AUTH_LOGIN_FAILED, str(exc), status_code=401) from exc
    except RuntimeError as exc:
        raise AppError(
            ErrorCode.AUTH_CONFIG_ERROR,
            "认证服务配置错误",
            status_code=500,
        ) from exc
