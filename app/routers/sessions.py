"""
sessions.py - 会话管理路由
=========================
对应 Spring 的 @RestController + Session CRUD。

- POST   /api/sessions                创建新会话
- GET    /api/sessions                列出所有会话
- GET    /api/sessions/{id}/messages  获取会话历史消息
- DELETE /api/sessions/{id}           删除会话（含记忆）
"""
import os
import json
import logging
from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text

from app.schemas.chat import SessionResponse
from app.services.agent_manager import agent_manager

router = APIRouter(prefix="/api", tags=["sessions"])
logger = logging.getLogger(__name__)


def _get_engine():
    return create_engine(os.getenv("MYSQL_URL"))


@router.post("/sessions", response_model=SessionResponse)
async def create_session():
    """创建新会话，返回 session_id。"""
    session_id = agent_manager.create_session()
    return SessionResponse(session_id=session_id)


@router.get("/sessions")
async def list_sessions():
    """列出所有会话（从 message_store 查询）。"""
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT session_id, MAX(created_at) as last_msg "
                "FROM message_store GROUP BY session_id ORDER BY last_msg DESC"
            )).fetchall()
        sessions = [
            {"session_id": r[0], "last_message_at": str(r[1])}
            for r in rows
        ]
    finally:
        engine.dispose()
    return {"sessions": sessions}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    """获取会话历史消息。"""
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
async def delete_session(session_id: str):
    """删除会话及其所有记忆数据。"""
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
