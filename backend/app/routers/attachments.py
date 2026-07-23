"""对话附件上传接口。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, UploadFile

from backend.app.dependencies.auth import get_current_user
from backend.app.errors import AppError, ErrorCode
from backend.app.routers.sessions import _verify_session_ownership
from backend.app.services.attachment_service import (
    MAX_ATTACHMENT_BYTES,
    save_attachment,
)
from backend.app.services.session_context_service import save_active_attachment


router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.post("")
async def upload_attachment(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传附件并绑定到当前会话，不执行任何业务导入。"""
    user_id = current_user["user_id"]
    _verify_session_ownership(session_id, user_id)

    content = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise AppError(
            ErrorCode.IMPORT_FILE_TOO_LARGE,
            f"附件不能超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB",
            status_code=413,
        )
    attachment = await asyncio.to_thread(
        save_attachment,
        user_id=user_id,
        session_id=session_id,
        filename=file.filename or "",
        content_type=file.content_type,
        content=content,
    )
    await asyncio.to_thread(save_active_attachment, session_id, user_id, attachment)
    return {"success": True, "attachment": attachment}
