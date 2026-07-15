"""会话管理路由。"""
import json
import logging
import os
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import create_engine, text

from backend.app.dependencies.auth import get_current_user
from backend.app.schemas.chat import SessionResponse
from backend.app.services.agent_manager import agent_manager
from backend.app.services.chart_snapshot_store import delete_chart_snapshots, load_chart_snapshots

router = APIRouter(prefix="/api", tags=["sessions"])
logger = logging.getLogger(__name__)


def _get_engine():
    """创建数据库引擎。"""
    mysql_url = os.getenv("MYSQL_URL")
    if not mysql_url:
        raise RuntimeError("MYSQL_URL 未配置")
    return create_engine(mysql_url)


def _verify_session_ownership(session_id: str, user_id: int) -> None:
    """校验会话属于当前用户。"""
    if session_id in agent_manager._agents:
        executor = agent_manager._agents[session_id]
        owner_id = getattr(executor.memory, "user_id", None)
        if owner_id is None:
            raise HTTPException(404, detail="会话缺少有效所有者信息")
        if owner_id != user_id:
            raise HTTPException(403, detail="无权操作此会话")
        return

    engine = _get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT user_id FROM message_store WHERE session_id = :sid "
                "ORDER BY created_at LIMIT 1"
            ), {"sid": session_id}).fetchone()
            if row is None:
                row = conn.execute(text(
                    "SELECT user_id FROM agent_summary_store WHERE session_id = :sid LIMIT 1"
                ), {"sid": session_id}).fetchone()
            if row is None:
                raise HTTPException(404, detail=f"会话 {session_id} 不存在")
            if row[0] is None:
                raise HTTPException(404, detail="会话缺少有效所有者信息")
            if int(row[0]) != user_id:
                raise HTTPException(403, detail="无权操作此会话")
    finally:
        engine.dispose()


@router.post("/sessions", response_model=SessionResponse)
async def create_session(current_user: dict = Depends(get_current_user)):
    """创建绑定当前用户的会话。"""
    session_id = agent_manager.create_session(user_id=current_user["user_id"])
    return SessionResponse(session_id=session_id)


@router.get("/sessions")
async def list_sessions(current_user: dict = Depends(get_current_user)):
    """列出当前用户的会话。"""
    user_id = current_user["user_id"]
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT session_id, MAX(created_at) AS last_msg FROM message_store "
                "WHERE user_id = :uid GROUP BY session_id ORDER BY last_msg DESC"
            ), {"uid": user_id}).fetchall()
        sessions = [{"session_id": r[0], "last_message_at": str(r[1])} for r in rows]
    finally:
        engine.dispose()
    for sid, executor in agent_manager._agents.items():
        if getattr(executor.memory, "user_id", None) == user_id and not any(s["session_id"] == sid for s in sessions):
            sessions.append({"session_id": sid, "last_message_at": None})
    return {"sessions": sessions}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, current_user: dict = Depends(get_current_user)):
    """Return conversation messages plus structured chart snapshots for ECharts."""
    _verify_session_ownership(session_id, current_user["user_id"])
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, message, created_at FROM message_store "
                "WHERE session_id = :sid AND user_id = :uid ORDER BY created_at, id"
            ), {"sid": session_id, "uid": current_user["user_id"]}).fetchall()
    finally:
        engine.dispose()

    messages = []
    message_index_by_id = {}
    assistant_indices = []
    for row in rows:
        try:
            msg = json.loads(row[1])
            msg_type = msg.get("type", "")
            if msg_type not in {"human", "user", "ai", "assistant"}:
                continue
            role = "user" if msg_type in {"human", "user"} else "assistant"
            item = {
                "role": role,
                "content": msg.get("data", {}).get("content", ""),
                "created_at": str(row[2]),
            }
            message_index_by_id[int(row[0])] = len(messages)
            if role == "assistant":
                assistant_indices.append(len(messages))
            messages.append(item)
        except (json.JSONDecodeError, IndexError, TypeError, ValueError):
            continue

    for snapshot in load_chart_snapshots(session_id, current_user["user_id"]):
        target_index = message_index_by_id.get(snapshot.get("message_id"))
        if target_index is None and assistant_indices:
            target_index = assistant_indices[-1]
        if target_index is None:
            continue
        charts = messages[target_index].setdefault("charts", [])
        charts.extend(snapshot.get("charts", []))
        if charts:
            messages[target_index]["chart_data"] = charts[0]

    return {"session_id": session_id, "messages": messages}

@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """删除当前用户的会话及其记忆。"""
    _verify_session_ownership(session_id, current_user["user_id"])
    agent_manager.remove_session(session_id)
    engine = _get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM message_store WHERE session_id = :sid AND user_id = :uid"), {"sid": session_id, "uid": current_user["user_id"]})
            conn.execute(text("DELETE FROM agent_summary_store WHERE session_id = :sid AND user_id = :uid"), {"sid": session_id, "uid": current_user["user_id"]})
        delete_chart_snapshots(session_id, current_user["user_id"])
    finally:
        engine.dispose()
    return {"status": "ok", "message": f"会话 {session_id} 已删除"}