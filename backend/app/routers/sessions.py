"""会话管理路由与会话元数据服务。"""
import base64
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from backend.app.dependencies.auth import get_current_user
from backend.app.schemas.chat import SessionResponse, SessionRenameRequest
from backend.app.services.agent_manager import agent_manager
from backend.app.services.chart_snapshot_store import delete_chart_snapshots, load_chart_snapshots
from backend.app.services.file_artifact_service import load_file_artifacts, sanitize_generated_file_text
from backend.app.database import get_engine
from backend.app.config import HISTORY_PAGE_SIZE, SESSION_PAGE_SIZE
from backend.app.errors import AppError, ErrorCode

router = APIRouter(prefix="/api", tags=["sessions"])
logger = logging.getLogger(__name__)

DEFAULT_SESSION_TITLE = "新会话"
MAX_SESSION_TITLE_LENGTH = 10


def _get_engine():
    """返回进程级共享 Engine；连接由调用方上下文管理器释放。"""
    return get_engine()


def build_session_title(message: str) -> str:
    """从首条用户消息生成不超过 10 个 Unicode 字符的会话名称。"""
    normalized = " ".join(str(message or "").split())
    title = "".join(list(normalized)[:MAX_SESSION_TITLE_LENGTH])
    return title or DEFAULT_SESSION_TITLE


def _encode_session_cursor(last_message_at, session_id: str) -> str:
    """Encode the last row of a page as an opaque keyset cursor."""
    payload = json.dumps(
        {"last_message_at": str(last_message_at), "session_id": session_id},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_session_cursor(cursor: str) -> tuple[str, str]:
    """Decode and validate a session-list keyset cursor."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        last_message_at = str(payload["last_message_at"])
        session_id = str(payload["session_id"])
        if not last_message_at or not session_id:
            raise ValueError
        return last_message_at, session_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise AppError(ErrorCode.SESSION_CURSOR_INVALID, "无效的会话分页游标", status_code=400)


def _extract_user_content(raw_message: str) -> str:
    """从 LangChain message_store JSON 中提取用户消息文本。"""
    try:
        payload = json.loads(raw_message)
        message_type = payload.get("type", "")
        if message_type not in {"human", "user"}:
            return ""
        return str(payload.get("data", {}).get("content", "") or "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return ""


def _extract_message_token_usage(payload: dict) -> dict | None:
    """从消息 JSON 的 response_metadata 恢复历史 token 用量。"""
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    metadata = data.get("response_metadata", {}) if isinstance(data, dict) else {}
    usage = metadata.get("token_usage") if isinstance(metadata, dict) else None
    if not isinstance(usage, dict):
        return None
    keys = {"input_tokens", "output_tokens", "total_tokens"}
    if not any(key in usage for key in keys):
        return None
    try:
        return {key: max(int(usage.get(key, 0) or 0), 0) for key in keys}
    except (TypeError, ValueError):
        return None


def _get_session_metadata(session_id: str, user_id: int):
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT session_id, user_id, title, title_source, last_message_at "
                "FROM chat_session WHERE session_id = :sid AND user_id = :uid"
            ), {"sid": session_id, "uid": user_id}).fetchone()
    finally:
        pass

def ensure_session_title(session_id: str, user_id: int, first_message: str = "") -> str:
    """确保会话元数据存在，并在自动命名状态下写入首条消息名称。"""
    title = build_session_title(first_message)
    engine = _get_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT title, title_source FROM chat_session "
                "WHERE session_id = :sid AND user_id = :uid FOR UPDATE"
            ), {"sid": session_id, "uid": user_id}).fetchone()

            if row is None:
                existing_message = conn.execute(text(
                    "SELECT message FROM message_store "
                    "WHERE session_id = :sid AND user_id = :uid "
                    "ORDER BY created_at, id LIMIT 1"
                ), {"sid": session_id, "uid": user_id}).fetchone()
                seed_message = (
                    _extract_user_content(existing_message[0])
                    if existing_message else first_message
                )
                title = build_session_title(seed_message)
                conn.execute(text(
                    "INSERT INTO chat_session "
                    "(session_id, user_id, title, title_source, last_message_at) "
                    "VALUES (:sid, :uid, :title, 'auto', NOW())"
                ), {"sid": session_id, "uid": user_id, "title": title})
                return title

            current_title = row[0] or DEFAULT_SESSION_TITLE
            if row[1] == "auto" and current_title == DEFAULT_SESSION_TITLE and first_message:
                conn.execute(text(
                    "UPDATE chat_session SET title = :title, last_message_at = NOW() "
                    "WHERE session_id = :sid AND user_id = :uid AND title_source = 'auto'"
                ), {"sid": session_id, "uid": user_id, "title": title})
                return title

            conn.execute(text(
                "UPDATE chat_session SET last_message_at = NOW() "
                "WHERE session_id = :sid AND user_id = :uid"
            ), {"sid": session_id, "uid": user_id})
            return current_title
    finally:
        pass

def _verify_session_ownership(session_id: str, user_id: int) -> None:
    """校验会话属于当前用户，优先使用会话元数据表。"""
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT user_id FROM chat_session WHERE session_id = :sid"
            ), {"sid": session_id}).fetchone()
            if row is None:
                row = conn.execute(text(
                    "SELECT user_id FROM message_store WHERE session_id = :sid "
                    "ORDER BY created_at, id LIMIT 1"
                ), {"sid": session_id}).fetchone()
            if row is None:
                row = conn.execute(text(
                    "SELECT user_id FROM agent_summary_store WHERE session_id = :sid LIMIT 1"
                ), {"sid": session_id}).fetchone()
            if row is None:
                raise AppError(ErrorCode.SESSION_NOT_FOUND, "会话不存在", status_code=404)
            if row[0] is None:
                raise AppError(ErrorCode.SESSION_NOT_FOUND, "会话缺少有效所有者信息", status_code=404)
            if int(row[0]) != user_id:
                raise AppError(ErrorCode.SESSION_FORBIDDEN, "无权操作此会话", status_code=403)
    finally:
        pass

@router.post("/sessions", response_model=SessionResponse)
async def create_session(current_user: dict = Depends(get_current_user)):
    """创建绑定当前用户的会话，并建立一条会话元数据记录。"""
    session_id = agent_manager.create_session(user_id=current_user["user_id"])
    title = ensure_session_title(session_id, current_user["user_id"])
    return SessionResponse(session_id=session_id, title=title)


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(SESSION_PAGE_SIZE, ge=1, le=100),
    before: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """按最后消息时间倒序返回当前用户的一页会话。"""
    user_id = current_user["user_id"]
    params = {"uid": user_id, "limit_plus_one": limit + 1}
    cursor_clause = ""
    if before:
        before_time, before_session_id = _decode_session_cursor(before)
        params.update({
            "before_time": before_time,
            "before_session_id": before_session_id,
        })
        cursor_clause = (
            " AND (last_message_at < :before_time "
            "OR (last_message_at = :before_time "
            "AND session_id < :before_session_id))"
        )

    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT session_id, title, last_message_at, updated_at "
            "FROM chat_session "
            "WHERE user_id = :uid"
            f"{cursor_clause} "
            "ORDER BY last_message_at DESC, session_id DESC "
            "LIMIT :limit_plus_one"
        ), params).fetchall()
        total = conn.execute(text(
            "SELECT COUNT(*) FROM chat_session WHERE user_id = :uid"
        ), {"uid": user_id}).scalar() or 0

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    sessions = [
        {
            "session_id": row[0],
            "title": row[1] or DEFAULT_SESSION_TITLE,
            "last_message_at": str(row[2] or row[3]) if (row[2] or row[3]) else None,
        }
        for row in page_rows
    ]
    next_cursor = None
    if has_more and page_rows:
        last_row = page_rows[-1]
        last_time = last_row[2] or last_row[3]
        if last_time:
            next_cursor = _encode_session_cursor(last_time, last_row[0])

    return {
        "sessions": sessions,
        "total": int(total),
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    limit: int = Query(HISTORY_PAGE_SIZE, ge=1, le=200),
    before_id: Optional[int] = Query(None, ge=1),
    current_user: dict = Depends(get_current_user),
):
    """返回会话消息、会话名称和结构化图表快照。"""
    user_id = current_user["user_id"]
    _verify_session_ownership(session_id, user_id)
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            # 查询会话标题 - 用于前端呈现当前会话的名称，若不存在则使用默认名称
            session_row = conn.execute(text(
                "SELECT title FROM chat_session "
                "WHERE session_id = :sid AND user_id = :uid"
            ), {"sid": session_id, "uid": user_id}).fetchone()
            # Message查询参数
            message_params = {
                "sid": session_id,
                "uid": user_id,
                # limit_plus_one 用于判断是否有更多消息可加载
                # 如果查到21条，说明还有更多消息。如果查到20条或更少，说明没有更多消息。
                "limit_plus_one": limit + 1,
            }
            # before_clause 是用于在查询中添加时间戳之前的条件，通过 before_id 参数来判断写什么
            before_clause = ""
            # 如果提供了 before_id，则在查询中添加条件，确保只获取 ID 小于 before_id 的消息
            if before_id is not None:
                before_clause = " AND id < :before_id"
                message_params["before_id"] = before_id
            # 倒序查询消息，以便获取最新的消息，并限制数量为 limit + 1
            rows_desc = conn.execute(text(
                "SELECT id, message, created_at FROM message_store "
                "WHERE session_id = :sid AND user_id = :uid"
                f"{before_clause} ORDER BY id DESC LIMIT :limit_plus_one"
            ), message_params).fetchall()
            # 判断是否有更多消息
            has_more = len(rows_desc) > limit
            # 去掉多查到的那条消息，只保留 limit 条消息，并将其顺序反转为正序
            rows = list(reversed(rows_desc[:limit]))
    finally:
        pass
    # 这部分负责把查到的原始数据，转换为前端可以直接展示的结果，并将图表快照附加到对应的消息上
    messages = []
    message_index_by_id = {}
    assistant_indices = []
    first_user_content = ""
    for row in rows:
        try:
            msg = json.loads(row[1])
            msg_type = msg.get("type", "")
            if msg_type not in {"human", "user", "ai", "assistant"}:
                continue
            role = "user" if msg_type in {"human", "user"} else "assistant"
            content = sanitize_generated_file_text(msg.get("data", {}).get("content", ""))
            item = {
                "id": int(row[0]),
                "role": role,
                "content": content,
                "created_at": str(row[2]),
            }
            token_usage = _extract_message_token_usage(msg)
            if token_usage:
                item["token_usage"] = token_usage
            if role == "user" and not first_user_content:
                first_user_content = str(content or "")
            message_index_by_id[int(row[0])] = len(messages)
            if role == "assistant":
                assistant_indices.append(len(messages))
            messages.append(item)
        except (json.JSONDecodeError, IndexError, TypeError, ValueError):
            continue

    title = session_row[0] if session_row else None
    if not title:
        title = build_session_title(first_user_content)

    message_ids = [int(row[0]) for row in rows]
    for snapshot in load_chart_snapshots(session_id, user_id, message_ids=message_ids):
        target_index = message_index_by_id.get(snapshot.get("message_id"))
        if target_index is None and assistant_indices:
            target_index = assistant_indices[-1]
        if target_index is None:
            continue
        charts = messages[target_index].setdefault("charts", [])
        charts.extend(snapshot.get("charts", []))
        if charts:
            messages[target_index]["chart_data"] = charts[0]

    # 生成文件和图表一样属于消息级产物；历史加载时恢复到原助手消息。
    for file_artifact in load_file_artifacts(session_id, user_id, message_ids=message_ids):
        target_index = message_index_by_id.get(file_artifact.get("message_id"))
        if target_index is None and assistant_indices:
            target_index = assistant_indices[-1]
        if target_index is None:
            continue
        files = messages[target_index].setdefault("files", [])
        if not any(item.get("file_id") == file_artifact.get("file_id") for item in files):
            files.append({
                key: file_artifact[key]
                for key in ("file_id", "filename", "content_type", "size_bytes", "status")
            })

    return {
        "session_id": session_id,
        "title": title,
        "messages": messages,
        "has_more": has_more,
        "next_before_id": int(rows[0][0]) if has_more and rows else None,
    }


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    req: SessionRenameRequest,
    current_user: dict = Depends(get_current_user),
):
    """手动重命名会话。"""
    user_id = current_user["user_id"]
    _verify_session_ownership(session_id, user_id)
    title = build_session_title(req.title)
    if title == DEFAULT_SESSION_TITLE and not str(req.title or "").strip():
        raise AppError(ErrorCode.SESSION_TITLE_INVALID, "会话名称不能为空", status_code=422)

    engine = _get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO chat_session "
                "(session_id, user_id, title, title_source) "
                "VALUES (:sid, :uid, :title, 'manual') "
                "ON DUPLICATE KEY UPDATE title = :title, title_source = 'manual'"
            ), {"sid": session_id, "uid": user_id, "title": title})
    finally:
        pass
    return {"session_id": session_id, "title": title}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """删除当前用户的会话及其记忆。"""
    user_id = current_user["user_id"]
    _verify_session_ownership(session_id, user_id)
    agent_manager.remove_session(session_id)
    engine = _get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM message_store WHERE session_id = :sid AND user_id = :uid"
            ), {"sid": session_id, "uid": user_id})
            conn.execute(text(
                "DELETE FROM agent_summary_store WHERE session_id = :sid AND user_id = :uid"
            ), {"sid": session_id, "uid": user_id})
            conn.execute(text(
                "DELETE FROM chat_session WHERE session_id = :sid AND user_id = :uid"
            ), {"sid": session_id, "uid": user_id})
        delete_chart_snapshots(session_id, user_id)
    finally:
        pass
    return {"status": "ok", "message": f"会话 {session_id} 已删除"}
