"""Agent 执行期间的附件上下文。

附件上下文只保存当前用户/会话允许使用的元数据，不保存文件内容。
工具拿到 attachment_id 后，再通过 AttachmentService 解析真实路径。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AttachmentExecutionContext:
    session_id: str
    user_id: int
    attachments: tuple[dict[str, Any], ...]


_current_context: contextvars.ContextVar[
    AttachmentExecutionContext | None
] = contextvars.ContextVar("current_attachment_context", default=None)


def bind_attachment_context(
    session_id: str,
    user_id: int,
    attachments: list[dict[str, Any]] | tuple[dict[str, Any], ...],
):
    """绑定一次 Agent 执行的附件范围。"""
    context = AttachmentExecutionContext(
        session_id=session_id,
        user_id=user_id,
        attachments=tuple(dict(item) for item in attachments),
    )
    return _current_context.set(context)


def get_attachment_context() -> AttachmentExecutionContext | None:
    return _current_context.get()


def reset_attachment_context(token) -> None:
    _current_context.reset(token)
