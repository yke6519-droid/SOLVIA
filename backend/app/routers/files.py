"""Agent 生成文件下载接口。"""

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from backend.app.dependencies.auth import get_current_user
from backend.app.services.file_artifact_service import get_owned_generated_file


router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/{file_id}/download")
async def download_generated_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """校验归属后，以附件下载形式返回 Agent 生成的文件。"""
    metadata, path = get_owned_generated_file(
        file_id,
        user_id=current_user["user_id"],
    )
    return FileResponse(
        path=str(path),
        filename=metadata["filename"],
        media_type=metadata["content_type"],
        headers={"Cache-Control": "private, no-store"},
    )
