"""Agent 生成文件的产物登记与下载存储服务。

Agent 和前端只使用 ``file_id``，不接触服务器真实路径。第一阶段使用本地
文件系统，后续可以把本服务内部的路径解析替换为阿里云 OSS，而不改工具和
前端下载接口。
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from backend.app.database import get_engine
from backend.app.errors import AppError, ErrorCode
from backend.app.services.attachment_context import get_attachment_context


logger = logging.getLogger(__name__)


# Agent 的自然语言可能复述旧工具返回的路径或 Markdown 链接。
# 这些内容不应进入前端可点击区域；真正的下载入口统一由文件卡片提供。
_FILE_LINK_LINE_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:文件路径|下载链接|下载地址|文件地址)\s*[:：].*(?:\r?\n|$)"
)
_FILE_API_LINK_RE = re.compile(
    r"\[[^\]]*(?:点击下载|下载文件|下载)[^\]]*\]\((?:https?://[^)]*)?/api/files/[^)]*\)",
    re.IGNORECASE,
)
_FILE_API_URL_RE = re.compile(r"(?:https?://[^\s)]+)?/api/files/[^\s)]+", re.IGNORECASE)
_LOCAL_FILE_PATH_RE = re.compile(
    r"`?(?:[A-Za-z]:[\\/]|/)(?:temp|uploads?)[\\/][^`\s)]+`?",
    re.IGNORECASE,
)


def sanitize_generated_file_text(content: str) -> str:
    """移除 Agent 文本中的文件路径和下载链接，只保留文件说明。"""
    cleaned = str(content or "")
    cleaned = _FILE_LINK_LINE_RE.sub("", cleaned)
    cleaned = _FILE_API_LINK_RE.sub("", cleaned)
    cleaned = _FILE_API_URL_RE.sub("", cleaned)
    cleaned = _LOCAL_FILE_PATH_RE.sub("", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _file_root() -> Path:
    """返回当前本地生成文件根目录；不把路径暴露给 Agent。"""
    configured = os.getenv("FILE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "temp" / "file").resolve()


def _safe_filename(filename: str) -> str:
    """只保留文件名本身，防止文件名携带目录穿越片段。"""
    safe_name = Path(filename or "").name.strip()
    if not safe_name or safe_name in {".", ".."}:
        raise AppError(ErrorCode.FILE_ARTIFACT_INVALID, "生成文件名无效", status_code=422)
    return safe_name


def _content_type(filename: str, explicit: str | None = None) -> str:
    """优先使用业务传入类型，无法识别时根据扩展名推断。"""
    if explicit:
        return explicit
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _resolve_local_path(path: str | Path) -> Path:
    """解析并校验生成文件路径必须位于 FILE_DIR 内。"""
    root = _file_root()
    resolved = Path(path).expanduser().resolve()
    if root != resolved and root not in resolved.parents:
        raise AppError(ErrorCode.FILE_ARTIFACT_INVALID, "生成文件路径不在允许的存储目录内", status_code=422)
    if not resolved.is_file():
        raise AppError(ErrorCode.FILE_ARTIFACT_NOT_FOUND, "生成文件不存在", status_code=404)
    return resolved


def register_generated_file(
    path: str | Path,
    *,
    user_id: int,
    session_id: str,
    filename: str | None = None,
    content_type: str | None = None,
    source_tool: str | None = None,
) -> dict[str, Any]:
    """登记一个已生成文件，并返回可供前端下载的安全元数据。"""
    resolved_path = _resolve_local_path(path)
    safe_name = _safe_filename(filename or resolved_path.name)
    digest = hashlib.sha256(resolved_path.read_bytes()).hexdigest()
    file_id = f"file_{uuid.uuid4().hex}"
    metadata = {
        "file_id": file_id,
        "filename": safe_name,
        "content_type": _content_type(safe_name, content_type),
        "size_bytes": resolved_path.stat().st_size,
        "status": "ready",
    }

    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO file_artifact "
                "(file_id, user_id, session_id, original_name, storage_uri, "
                "content_type, size_bytes, sha256, source_tool, status) "
                "VALUES (:fid, :uid, :sid, :name, :uri, :ctype, :size, :sha, :tool, 'ready')"
            ),
            {
                "fid": file_id,
                "uid": user_id,
                "sid": session_id,
                "name": safe_name,
                "uri": str(resolved_path),
                "ctype": metadata["content_type"],
                "size": metadata["size_bytes"],
                "sha": digest,
                "tool": source_tool,
            },
        )
    return metadata


def _latest_assistant_message_id(conn, session_id: str, user_id: int) -> int | None:
    """查找当前会话最近一条助手消息，用于绑定本轮生成文件。"""
    rows = conn.execute(
        text(
            "SELECT id, message FROM message_store "
            "WHERE session_id = :sid AND user_id = :uid "
            "ORDER BY id DESC LIMIT 30"
        ),
        {"sid": session_id, "uid": user_id},
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row[1])
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("type") in {"ai", "assistant"}:
            return int(row[0])
    return None


def attach_files_to_latest_assistant_message(
    session_id: str,
    user_id: int,
    file_ids: Iterable[str],
) -> None:
    """把本轮生成的文件产物绑定到最近助手消息。"""
    unique_file_ids = list(dict.fromkeys(str(item) for item in file_ids if item))
    if not unique_file_ids:
        return
    try:
        with get_engine().begin() as conn:
            message_id = _latest_assistant_message_id(conn, session_id, user_id)
            if message_id is None:
                logger.warning("未找到助手消息，暂不绑定生成文件 session=%s", session_id)
                return
            for file_id in unique_file_ids:
                conn.execute(
                    text(
                        "UPDATE file_artifact SET message_id = :mid "
                        "WHERE file_id = :fid AND user_id = :uid AND session_id = :sid"
                    ),
                    {"mid": message_id, "fid": file_id, "uid": user_id, "sid": session_id},
                )
    except Exception:
        # 文件已经生成，绑定失败不影响本轮任务；历史卡片只会暂时缺失。
        logger.exception("生成文件绑定助手消息失败 session=%s", session_id)


def attach_token_usage_to_latest_assistant_message(
    session_id: str,
    user_id: int,
    token_usage: dict[str, int] | None,
) -> None:
    """将本轮 token 用量写入已有的 LangChain 助手消息 JSON。

    不新增一张表：message_store 本身就是消息事实源，usage 作为
    response_metadata.token_usage 的附加字段保存，历史消息接口可以直接恢复。
    """
    if not isinstance(token_usage, dict) or not token_usage:
        return
    try:
        with get_engine().begin() as conn:
            message_id = _latest_assistant_message_id(conn, session_id, user_id)
            if message_id is None:
                logger.warning("未找到助手消息，暂不保存 token 用量 session=%s", session_id)
                return
            row = conn.execute(
                text(
                    "SELECT message FROM message_store "
                    "WHERE id = :mid AND session_id = :sid AND user_id = :uid"
                ),
                {"mid": message_id, "sid": session_id, "uid": user_id},
            ).fetchone()
            if not row:
                return
            payload = json.loads(row[0])
            data = payload.setdefault("data", {})
            metadata = data.setdefault("response_metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
                data["response_metadata"] = metadata
            metadata["token_usage"] = {
                key: int(value or 0)
                for key, value in token_usage.items()
                if key in {"input_tokens", "output_tokens", "total_tokens"}
            }
            conn.execute(
                text(
                    "UPDATE message_store SET message = :message "
                    "WHERE id = :mid AND session_id = :sid AND user_id = :uid"
                ),
                {
                    "message": json.dumps(payload, ensure_ascii=False),
                    "mid": message_id,
                    "sid": session_id,
                    "uid": user_id,
                },
            )
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.exception("助手消息 token 用量序列化失败 session=%s", session_id)
    except Exception:
        # token 展示是辅助信息，保存失败不应影响已经完成的 Agent 任务。
        logger.exception("助手消息 token 用量保存失败 session=%s", session_id)


def load_file_artifacts(
    session_id: str,
    user_id: int,
    *,
    message_ids: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    """按会话和消息 ID 读取生成文件卡片元数据。"""
    selected_ids = [int(item) for item in (message_ids or [])]
    if not selected_ids:
        return []
    try:
        with get_engine().connect() as conn:
            params: dict[str, Any] = {"sid": session_id, "uid": user_id}
            placeholders = ", ".join(
                f":message_id_{index}" for index in range(len(selected_ids))
            )
            params.update({
                f"message_id_{index}": message_id
                for index, message_id in enumerate(selected_ids)
            })
            rows = conn.execute(
                text(
                    "SELECT file_id, message_id, original_name, content_type, "
                    "size_bytes, status FROM file_artifact "
                    "WHERE session_id = :sid AND user_id = :uid "
                    f"AND message_id IN ({placeholders}) "
                    "AND status NOT IN ('expired', 'deleted') "
                    "ORDER BY created_at, file_id"
                ),
                params,
            ).fetchall()
        return [
            {
                "file_id": row[0],
                "message_id": int(row[1]) if row[1] is not None else None,
                "filename": row[2],
                "content_type": row[3] or "application/octet-stream",
                "size_bytes": int(row[4]),
                "status": row[5],
            }
            for row in rows
        ]
    except Exception:
        logger.warning("读取生成文件历史失败，请执行文件产物迁移", exc_info=True)
        return []


def register_current_generated_file(
    path: str | Path,
    *,
    filename: str | None = None,
    content_type: str | None = None,
    source_tool: str | None = None,
) -> dict[str, Any] | None:
    """使用当前 Agent 执行上下文登记文件；无上下文时保持工具离线调用兼容。"""
    context = get_attachment_context()
    if context is None:
        return None
    try:
        return register_generated_file(
            path,
            user_id=context.user_id,
            session_id=context.session_id,
            filename=filename,
            content_type=content_type,
            source_tool=source_tool,
        )
    except Exception:
        # 文件本体已经生成时，登记失败不应回滚文件生成；同时不能把真实路径
        # 退回给 Agent。前端会根据缺少 file_id 判断当前文件暂不可下载。
        logger.exception("生成文件登记失败 session=%s", context.session_id)
        return {
            "file_id": "",
            "filename": Path(filename or path).name,
            "content_type": _content_type(Path(filename or path).name, content_type),
            "size_bytes": 0,
            "status": "generated_unregistered",
        }


def get_owned_generated_file(
    file_id: str,
    *,
    user_id: int,
    session_id: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """按 file_id 查询文件并校验用户、会话和本地文件归属。"""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT file_id, user_id, session_id, original_name, storage_uri, "
                "content_type, size_bytes, status "
                "FROM file_artifact WHERE file_id = :fid"
            ),
            {"fid": file_id},
        ).fetchone()
    if row is None:
        raise AppError(ErrorCode.FILE_ARTIFACT_NOT_FOUND, "生成文件不存在", status_code=404)
    if int(row[1]) != int(user_id):
        raise AppError(ErrorCode.FILE_ARTIFACT_FORBIDDEN, "无权下载该生成文件", status_code=403)
    if session_id is not None and row[2] != session_id:
        raise AppError(ErrorCode.FILE_ARTIFACT_FORBIDDEN, "文件不属于当前会话", status_code=403)
    if row[7] in {"expired", "deleted"}:
        raise AppError(ErrorCode.FILE_ARTIFACT_EXPIRED, "生成文件已过期或已删除", status_code=410)

    path = _resolve_local_path(row[4])
    return {
        "file_id": row[0],
        "filename": row[3],
        "content_type": row[5] or "application/octet-stream",
        "size_bytes": int(row[6]),
        "status": row[7],
    }, path
