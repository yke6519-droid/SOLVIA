"""Execution context used by chart tools for artifact ownership checks."""

import contextvars
from dataclasses import dataclass


@dataclass
class ChartExecutionContext:
    session_id: str
    user_id: int


_current_context: contextvars.ContextVar[ChartExecutionContext | None] = contextvars.ContextVar(
    "chart_execution_context",
    default=None,
)


def bind_chart_context(session_id: str, user_id: int):
    return _current_context.set(ChartExecutionContext(session_id=session_id, user_id=user_id))


def reset_chart_context(token) -> None:
    _current_context.reset(token)


def get_chart_context() -> ChartExecutionContext:
    context = _current_context.get()
    if context is None:
        raise RuntimeError("图表工具缺少当前会话上下文")
    return context
