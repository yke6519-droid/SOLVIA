"""附件存储与权限服务。

第一阶段使用本地文件存储，但数据库保存真实 storage_uri。Agent 只能拿到
attachment_id，真实路径必须由本服务在校验用户和会话归属后解析。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.app.database import get_engine
from backend.app.errors import AppError, ErrorCode


ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".txt", ".md"}
MAX_ATTACHMENT_BYTES = int(
    os.getenv("ATTACHMENT_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))
)


def _storage_root() -> Path:
    configured = os.getenv("FILE_DIR")
    if configured:
        return Path(configured).expanduser().resolve() / "uploads"
    return (Path(__file__).resolve().parents[2] / "temp" / "file" / "uploads").resolve()


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "").name.strip()
    extension = Path(name).suffix.lower()
    if not name or extension not in ALLOWED_EXTENSIONS:
        raise AppError(
            ErrorCode.ATTACHMENT_INVALID,
            "附件格式不支持，仅支持 Excel、CSV、TXT 和 Markdown 文件",
            status_code=400,
            details={"allowed_extensions": sorted(ALLOWED_EXTENSIONS)},
        )
    return name


def _attachment_row(row) -> dict[str, Any]:
    return {
        "attachment_id": row[0],
        "user_id": int(row[1]),
        "session_id": row[2],
        "filename": row[3],
        "storage_uri": row[4],
        "content_type": row[5] or "application/octet-stream",
        "size_bytes": int(row[6]),
        "sha256": row[7],
        "status": row[8],
        "created_at": str(row[9]) if row[9] is not None else None,
        "expires_at": str(row[10]) if row[10] is not None else None,
    }


def save_attachment(
    *,
    user_id: int,
    session_id: str,
    filename: str,
    content_type: str | None,
    content: bytes,
) -> dict[str, Any]:
    """保存附件文件和元数据，返回可安全暴露给前端/Agent 的摘要。"""
    safe_name = _safe_filename(filename)
    if not content:
        raise AppError(ErrorCode.ATTACHMENT_INVALID, "附件内容为空", status_code=400)
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise AppError(
            ErrorCode.IMPORT_FILE_TOO_LARGE,
            f"附件不能超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB",
            status_code=413,
            details={"max_bytes": MAX_ATTACHMENT_BYTES},
        )

    attachment_id = f"att_{uuid.uuid4().hex}"
    extension = Path(safe_name).suffix.lower()
    target_dir = _storage_root() / str(user_id) / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = (target_dir / f"{attachment_id}{extension}").resolve()
    root = _storage_root()
    if root not in target_path.parents:
        raise AppError(ErrorCode.ATTACHMENT_INVALID, "附件存储路径非法", status_code=400)

    digest = hashlib.sha256(content).hexdigest()
    try:
        target_path.write_bytes(content)
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO file_attachment "
                    "(attachment_id, user_id, session_id, original_name, storage_uri, "
                    "content_type, size_bytes, sha256, status) "
                    "VALUES (:aid, :uid, :sid, :name, :uri, :ctype, :size, :sha, 'uploaded')"
                ),
                {
                    "aid": attachment_id,
                    "uid": user_id,
                    "sid": session_id,
                    "name": safe_name,
                    "uri": str(target_path),
                    "ctype": content_type or "application/octet-stream",
                    "size": len(content),
                    "sha": digest,
                },
            )
    except Exception:
        target_path.unlink(missing_ok=True)
        raise

    return {
        "attachment_id": attachment_id,
        "filename": safe_name,
        "content_type": content_type or "application/octet-stream",
        "size_bytes": len(content),
        "status": "uploaded",
    }


def get_owned_attachment(
    attachment_id: str,
    *,
    user_id: int,
    session_id: str | None = None,
) -> dict[str, Any]:
    """按 ID 查询附件，并强制校验用户和会话归属。"""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT attachment_id, user_id, session_id, original_name, storage_uri, "
                "content_type, size_bytes, sha256, status, created_at, expires_at "
                "FROM file_attachment WHERE attachment_id = :aid"
            ),
            {"aid": attachment_id},
        ).fetchone()
    if row is None:
        raise AppError(ErrorCode.ATTACHMENT_NOT_FOUND, "附件不存在", status_code=404)
    attachment = _attachment_row(row)
    if attachment["user_id"] != int(user_id):
        raise AppError(ErrorCode.ATTACHMENT_FORBIDDEN, "无权访问该附件", status_code=403)
    if session_id is not None and attachment["session_id"] != session_id:
        raise AppError(ErrorCode.ATTACHMENT_FORBIDDEN, "附件不属于当前会话", status_code=403)
    if attachment["status"] in {"expired", "deleted"}:
        raise AppError(ErrorCode.ATTACHMENT_EXPIRED, "附件已过期或已删除", status_code=410)
    return attachment


def load_owned_attachments(
    attachment_ids: list[str],
    *,
    user_id: int,
    session_id: str,
) -> list[dict[str, Any]]:
    """批量解析当前请求的附件，保持用户传入顺序。"""
    unique_ids = list(dict.fromkeys(attachment_ids))
    if len(unique_ids) > 5:
        raise AppError(ErrorCode.ATTACHMENT_INVALID, "单条消息最多携带5个附件", status_code=400)
    return [
        get_owned_attachment(attachment_id, user_id=user_id, session_id=session_id)
        for attachment_id in unique_ids
    ]


def resolve_attachment_path(
    attachment_id: str,
    *,
    user_id: int,
    session_id: str,
) -> tuple[dict[str, Any], Path]:
    """把已授权附件 ID 解析成本地路径，供已有文件工具复用。"""
    attachment = get_owned_attachment(
        attachment_id,
        user_id=user_id,
        session_id=session_id,
    )
    path = Path(attachment["storage_uri"]).resolve()
    if not path.is_file():
        raise AppError(ErrorCode.ATTACHMENT_NOT_FOUND, "附件文件已不存在", status_code=404)
    return attachment, path


def resolve_bound_attachment_path(attachment_id: str) -> tuple[dict[str, Any], Path]:
    """根据当前 Agent 执行上下文解析附件，供多个文件工具复用。"""
    from backend.app.services.attachment_context import get_attachment_context

    context = get_attachment_context()
    if context is None:
        raise AppError(ErrorCode.ATTACHMENT_INVALID, "当前没有可用的附件执行上下文", status_code=422)
    allowed_ids = {item.get("attachment_id") for item in context.attachments}
    if attachment_id not in allowed_ids:
        raise AppError(ErrorCode.ATTACHMENT_FORBIDDEN, "附件不属于当前任务或当前会话", status_code=403)
    return resolve_attachment_path(
        attachment_id,
        user_id=context.user_id,
        session_id=context.session_id,
    )


def build_attachment_context(attachments: list[dict[str, Any]]) -> str:
    """生成注入 Agent 输入的紧凑附件上下文，不暴露 storage_uri。"""
    if not attachments:
        return ""
    lines = [
        "[系统附件上下文：仅用于选择工具，不要向用户暴露服务器真实路径]",
        "当前可用附件：",
    ]
    for item in attachments:
        lines.append(
            f"- attachment_id={item['attachment_id']}; "
            f"filename={item['filename']}; "
            f"content_type={item['content_type']}; size_bytes={item['size_bytes']}"
        )
    lines.append("如果用户明确要求处理附件，必须把对应 attachment_id 传给工具；没有附件相关意图时不要主动处理文件。")
    return "\n".join(lines)


def encode_attachment_context(context: dict[str, Any]) -> str:
    """仅用于日志/调试的安全 JSON，不包含 storage_uri。"""
    return json.dumps(context, ensure_ascii=False)
