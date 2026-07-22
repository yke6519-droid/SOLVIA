"""前端文件导入接口。"""

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from langchain_core.tools import ToolException

from backend.app.dependencies.auth import get_current_user
from backend.app.errors import AppError, ErrorCode
from backend.tools.import_tool import execute_import, parse_excel_preview


router = APIRouter(prefix="/api/import", tags=["import"])

ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
MAX_UPLOAD_BYTES = int(os.getenv("IMPORT_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))


def _validate_filename(filename: str | None) -> str:
    """只保留文件名和扩展名，不让客户端文件名参与服务器路径拼接。"""
    safe_name = Path(filename or "").name
    extension = Path(safe_name).suffix.lower()
    if not safe_name or extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise AppError(
            ErrorCode.IMPORT_FILE_INVALID,
            f"仅支持 {allowed} 格式的发电量文件",
            status_code=400,
            details={"allowed_extensions": sorted(ALLOWED_EXTENSIONS)},
        )
    return safe_name


async def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    """读取上传文件，并在进入 Excel 解析前完成大小和扩展名校验。"""
    filename = _validate_filename(file.filename)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise AppError(ErrorCode.IMPORT_FILE_INVALID, "上传文件为空", status_code=400)
    if len(content) > MAX_UPLOAD_BYTES:
        raise AppError(
            ErrorCode.IMPORT_FILE_TOO_LARGE,
            f"文件不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
            status_code=413,
            details={"max_bytes": MAX_UPLOAD_BYTES},
        )
    return filename, content


@router.post("/power/preview")
async def preview_power_import(
    file: UploadFile = File(...),
    skip_clean: bool = Form(False),
    current_user: dict = Depends(get_current_user),
):
    """解析并清洗前端上传的发电量文件，但不写入数据库。"""
    del current_user  # 依赖本身用于认证，导入结果不需要绑定用户字段。
    filename, content = await _read_upload(file)
    try:
        # Excel 解析属于 CPU/IO 混合型同步任务，放到线程池中，避免阻塞
        # FastAPI 的事件循环以及同一进程中的其他请求。
        preview = await asyncio.to_thread(
            parse_excel_preview,
            file_bytes=content,
            filename=filename,
            skip_clean=skip_clean,
        )
    except ToolException as exc:
        raise AppError(
            ErrorCode.IMPORT_PREVIEW_FAILED,
            str(exc),
            status_code=422,
        ) from exc
    except Exception as exc:
        raise AppError(
            ErrorCode.IMPORT_PREVIEW_FAILED,
            "文件解析失败，请检查 Excel 格式和内容",
            status_code=422,
        ) from exc

    return {"success": True, "preview": preview}


@router.post("/power/execute")
async def execute_power_import(
    file: UploadFile = File(...),
    skip_clean: bool = Form(False),
    current_user: dict = Depends(get_current_user),
):
    """清洗并将前端确认后的发电量文件写入 MySQL。"""
    del current_user
    filename, content = await _read_upload(file)
    try:
        result = await asyncio.to_thread(
            execute_import,
            file_bytes=content,
            filename=filename,
            skip_clean=skip_clean,
        )
    except ToolException as exc:
        raise AppError(
            ErrorCode.IMPORT_EXECUTION_FAILED,
            str(exc),
            status_code=422,
        ) from exc
    except Exception as exc:
        raise AppError(
            ErrorCode.IMPORT_EXECUTION_FAILED,
            "数据入库失败，请检查数据库连接和文件内容",
            status_code=500,
            retryable=True,
        ) from exc

    return result
