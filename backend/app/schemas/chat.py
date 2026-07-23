"""
chat.py - 请求/响应数据模型
==========================
对应 Spring 的 DTO / VO 概念。
"""
from pydantic import BaseModel, Field
from typing import List, Optional


class ChatAttachment(BaseModel):
    """用户消息引用的已上传附件，只传递不透明的附件 ID。"""

    attachment_id: str = Field(..., min_length=8, max_length=64)


class ChatRequest(BaseModel):
    """对话请求"""
    session_id: str = Field(..., description="会话ID")
    message: str = Field(..., description="用户消息")
    attachments: List[ChatAttachment] = Field(
        default_factory=list,
        max_length=5,
        description="当前消息携带的附件 ID，最多5个",
    )



class ReplyRequest(BaseModel):
    """ask_user 回复请求"""
    answer: str = Field(..., description="用户回复内容")


class SessionResponse(BaseModel):
    """会话创建响应"""
    session_id: str
    title: str = "新会话"
    message: str = "会话已创建"


class SessionRenameRequest(BaseModel):
    """会话重命名请求"""
    title: str = Field(..., min_length=1, max_length=10, description="会话名称，最长10个字")


class MessageItem(BaseModel):
    """单条历史消息"""
    role: str  # "user" | "assistant"
    content: str
    created_at: Optional[str] = None
