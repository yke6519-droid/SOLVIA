"""RuntimeContext 的任务级 ContextVar 绑定。"""

from __future__ import annotations

import contextvars

from backend.app.runtime.enums import AgentRunState
from backend.app.runtime.exceptions import RuntimeFatalError
from backend.app.runtime.models import RuntimeContext


_current_context: contextvars.ContextVar[RuntimeContext | None] = contextvars.ContextVar(
    "runtime_execution_context",
    default=None,
)


def bind_runtime_context(session_id: str, user_id: int):
    """在当前 Agent 执行 Task 中绑定一个新的 RuntimeContext。"""

    context = RuntimeContext(
        session_id=session_id,
        user_id=user_id,
        state=AgentRunState.CREATED,
    )
    return _current_context.set(context)


def get_runtime_context(*, required: bool = True) -> RuntimeContext | None:
    """读取当前任务的 RuntimeContext。"""

    context = _current_context.get()
    if context is None and required:
        raise RuntimeFatalError(
            "RUNTIME_CONTEXT_MISSING",
            "当前 Runtime 任务缺少执行上下文",
        )
    return context


def reset_runtime_context(token) -> None:
    """恢复绑定前的上下文，避免本轮状态泄漏到后续请求。"""

    _current_context.reset(token)

