"""Execution context used by chart tools for artifact ownership checks."""

import contextvars
from dataclasses import dataclass


CHART_PLAN_MAX_ATTEMPTS = 2


@dataclass
class ChartExecutionContext:
    session_id: str
    user_id: int
    capability_preflight_done: bool = False
    chart_plan_attempts: int = 0


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


def mark_capability_preflight() -> None:
    """Mark that the current Agent turn has inspected the capability registry."""

    context = _current_context.get()
    if context is not None:
        context.capability_preflight_done = True


def capability_preflight_done() -> bool:
    context = _current_context.get()
    return bool(context and context.capability_preflight_done)


def consume_chart_plan_attempt() -> tuple[int, int]:
    """Consume one plan submission and return (attempt, max_attempts).

    The counter is scoped to the current chart execution context, so ordinary
    tools such as prediction, ask_user, and dataset loading do not consume the
    chart-plan retry budget.
    """

    context = _current_context.get()
    if context is None:
        # Direct service/tool tests without a web request have no turn scope;
        # leave them usable while production requests remain guarded.
        return 0, CHART_PLAN_MAX_ATTEMPTS
    context.chart_plan_attempts += 1
    return context.chart_plan_attempts, CHART_PLAN_MAX_ATTEMPTS
