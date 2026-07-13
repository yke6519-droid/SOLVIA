"""
sessions.py - 会话管理路由
=========================
对应 Spring 的 @RestController + Session CRUD。

安全性: 所有涉及会话操作的接口都要求传 user_id，并通过 _verify_session_ownership
        校验该会话是否属于该用户，防止越权访问。

- POST   /api/sessions?user_id=X              创建新会话（绑定 user_id）
- GET    /api/sessions?user_id=X              列出某用户的所有会话
- GET    /api/sessions/{id}/messages?user_id=X  获取会话历史消息（校验所有权）
- DELETE /api/sessions/{id}?user_id=X         删除会话（校验所有权）
"""
import os
import json
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import create_engine, text

from app.schemas.chat import SessionResponse
from app.services.agent_manager import agent_manager

router = APIRouter(prefix="/api", tags=["sessions"])
logger = logging.getLogger(__name__)


def _get_engine():
    return create_engine(os.getenv("MYSQL_URL"))


def _verify_session_ownership(session_id: str, user_id: int) -> None:
    """
    校验会话所有权: 确认该 session_id 属于该 user_id。

    查找顺序:
      1. agent_manager 内存缓存（刚创建还没对话的会话）
      2. message_store（已有对话记录）
      3. agent_summary_store（有摘要但消息已清理）

    不匹配则抛 403，找不到则抛 404。
    """
    # 1. 先查内存缓存
    if session_id in agent_manager._agents:
        executor = agent_manager._agents[session_id]
        owner_id = getattr(executor.memory, 'user_id', None)
        if owner_id is not None and owner_id != user_id:
            raise HTTPException(403, detail="无权操作此会话: 该会话不属于当前用户")
        return

    # 2. 再查数据库
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT user_id FROM message_store WHERE session_id = :sid LIMIT 1"
            ), {"sid": session_id}).fetchone()

            if row is None:
                row = conn.execute(text(
                    "SELECT user_id FROM agent_summary_store WHERE session_id = :sid"
                ), {"sid": session_id}).fetchone()

            if row is None:
                raise HTTPException(404, detail=f"会话 {session_id} 不存在")

            owner_id = row[0]
            if owner_id is not None and owner_id != user_id:
                raise HTTPException(403, detail="无权操作此会话: 该会话不属于当前用户")
    finally:
        engine.dispose()


@router.post("/sessions", response_model=SessionResponse)
async def create_session(user_id: int = Query(..., description="用户ID，必传，用于绑定会话所有者")):
    """创建新会话，返回 session_id。会话所有者为 user_id。"""
    session_id = agent_manager.create_session(user_id=user_id)
    return SessionResponse(session_id=session_id)


@router.get("/sessions")
async def list_sessions(user_id: int = Query(..., description="用户ID，必传，只返回该用户的会话")):
    """列出某用户的所有会话。"""
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT session_id, MAX(created_at) as last_msg "
                "FROM message_store WHERE user_id = :uid "
                "GROUP BY session_id ORDER BY last_msg DESC"
            ), {"uid": user_id}).fetchall()
        sessions = [
            {"session_id": r[0], "last_message_at": str(r[1])}
            for r in rows
        ]
    finally:
        engine.dispose()
    return {"sessions": sessions}


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    user_id: int = Query(..., description="用户ID，必传，校验会话所有权"),
):
    """获取会话历史消息。校验该会话是否属于当前用户。"""
    _verify_session_ownership(session_id, user_id)

    engine = _get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT message, created_at FROM message_store "
                "WHERE session_id = :sid ORDER BY created_at"
            ), {"sid": session_id}).fetchall()
    finally:
        engine.dispose()

    messages = []
    for row in rows:
        try:
            msg = json.loads(row[0])
            msg_type = msg.get("type", "")
            content = msg.get("data", {}).get("content", "")
            messages.append({
                "role": "user" if msg_type == "human" else "assistant",
                "content": content,
                "created_at": str(row[1])
            })
        except (json.JSONDecodeError, IndexError):
            continue

    return {"session_id": session_id, "messages": messages}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_id: int = Query(..., description="用户ID，必传，校验会话所有权"),
):
    """删除会话及其所有记忆数据。校验该会话是否属于当前用户。"""
    _verify_session_ownership(session_id, user_id)

    # 清理内存缓存
    if session_id in agent_manager._agents:
        del agent_manager._agents[session_id]
    if session_id in agent_manager._bridges:
        del agent_manager._bridges[session_id]

    # 清理数据库
    engine = _get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM message_store WHERE session_id = :sid"
            ), {"sid": session_id})
            conn.execute(text(
                "DELETE FROM agent_summary_store WHERE session_id = :sid"
            ), {"sid": session_id})
    finally:
        engine.dispose()

    return {"status": "ok", "message": f"会话 {session_id} 已删除"}
