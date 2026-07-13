"""
auth.py - 认证路由
==================
对应 Spring 的 @RestController + @RequestMapping("/api/auth")。

当前阶段:
  - 注册/登录返回 user_id，前端存储后请求时携带
  - 暂不做 JWT Token，前后端分离时加
"""
import logging
from fastapi import APIRouter, HTTPException

from app.schemas.user import RegisterRequest, LoginRequest, UserResponse
from app.services.user_service import register_user, login_user

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
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/login", response_model=UserResponse)
async def login(req: LoginRequest):
    """用户登录。"""
    try:
        user = login_user(req.username, req.password)
        return UserResponse(
            user_id=user["user_id"],
            username=user["username"],
            role=user["role"],
            display_name=user["display_name"],
            message="登录成功",
        )
    except ValueError as e:
        raise HTTPException(401, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(403, detail=str(e))
