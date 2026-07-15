"""
chat.py - 请求/响应数据模型
==========================
对应 Spring 的 DTO / VO 概念。
"""
from pydantic import BaseModel, Field
from typing import List, Optional


class ChatRequest(BaseModel):
    """对话请求"""
    session_id: str = Field(..., description="会话ID")
    message: str = Field(..., description="用户消息")



class ReplyRequest(BaseModel):
    """ask_user 回复请求"""
    answer: str = Field(..., description="用户回复内容")


class SessionResponse(BaseModel):
    """会话创建响应"""
    session_id: str
    message: str = "会话已创建"


class MessageItem(BaseModel):
    """单条历史消息"""
    role: str  # "user" | "assistant"
    content: str
    created_at: Optional[str] = None
