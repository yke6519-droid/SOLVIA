"""
user.py - 用户相关请求/响应模型
================================
对应 Spring 的 DTO/VO。
"""


from typing import Optional
from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """注册请求"""
    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")
    display_name: Optional[str] = Field(None, max_length=128, description="显示名称")


class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class UserResponse(BaseModel):
    """用户信息响应"""
    user_id: int = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    role: str = Field(..., description="角色: super_admin/admin/user")
    display_name: Optional[str] = Field(None, description="显示名称")
message: str = Field("操作成功", description="提示消息")


class AuthResponse(BaseModel):
    """登录响应，兼容 Vue/Axios/fetch 等前端。"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
