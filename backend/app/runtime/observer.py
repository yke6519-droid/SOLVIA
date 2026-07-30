"""LangChain 事件到 Runtime AgentEvent 的旁路观察器。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from langchain_core.tools import ToolException

from backend.app.runtime.context import (
    RuntimeInvocationBridge,
    get_runtime_invocation_bridge,
)
from backend.app.runtime.enums import (
    AgentEventType,
    AgentRunState,
    ToolResultStatus,
)
from backend.app.runtime.exceptions import RuntimeFatalError, RuntimeInteractionError
from backend.app.runtime.models import (
    AgentEvent,
    RuntimeContext,
    ToolInvocation,
    ToolResult,
)
from backend.app.runtime.result_normalizer import ResultNormalizer


logger = logging.getLogger(__name__)
EventSink = Callable[[AgentEvent], None]

_REDACTED_KEYS = {
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "secret",
    "private_key",
    "path",
    "file_path",
    "content",
    "data",
}
_MAX_TEXT_LENGTH = 160


def _utc_now() -> datetime:
    """生成旁路事件使用的 UTC 时间。"""

    return datetime.now(timezone.utc)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return lowered in _REDACTED_KEYS or any(
        marker in lowered
        for marker in ("password", "token", "secret", "authorization")
    )


def _summarize_value(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    """只保留参数摘要，避免 Runtime 日志暴露密码、路径或完整数据行。"""

    if key and _is_sensitive_key(key):
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= _MAX_TEXT_LENGTH:
            return value
        return f"{value[:_MAX_TEXT_LENGTH]}..."
    if isinstance(value, (list, tuple, set)):
        # 普通列表可能包含大量原始数据，旁路只记录数量。
        # ChartPlan 的 series 是安全的小对象列表，需要保留 field/name，
        # 才能看清 Agent 每次尝试绑定了哪个字段，同时不记录原始数据行。
        if key == "series" and depth < 3 and len(value) <= 10:
            items = []
            for item in value:
                if not isinstance(item, dict):
                    items.append({"type": type(item).__name__})
                    continue
                items.append({
                    item_key: _summarize_value(item[item_key], key=item_key, depth=depth + 1)
                    for item_key in ("field", "name")
                    if item_key in item
                })
            return {"type": "list", "length": len(value), "items": items}
        # 其他列表可能包含完整业务数据，旁路只记录数量，不记录每一行内容。
        return {"type": "list", "length": len(value)}
    if isinstance(value, dict):
        if depth >= 3:
            return {"type": "object", "keys": [str(item) for item in value.keys()]}
        return {
            str(item_key): _summarize_value(item, key=str(item_key), depth=depth + 1)
            for item_key, item in value.items()
        }
    return {"type": type(value).__name__}


def _summarize_arguments(value: Any) -> dict[str, Any]:
    """保证工具参数摘要始终是对象，兼容无参数或非字典输入。"""

    summary = _summarize_value(value if value is not None else {})
    if isinstance(summary, dict):
        return summary
    return {"value": summary}


def _safe_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    return text if len(text) <= _MAX_TEXT_LENGTH else f"{text[:_MAX_TEXT_LENGTH]}..."


def _failure_info(error: Any) -> tuple[str, str, str, dict[str, Any]]:
    """区分可预期工具失败、Runtime 严重异常和未知程序异常。"""

    if error is None:
        return "recoverable", "TOOL_ERROR", "工具执行失败", {}
    if isinstance(error, RuntimeFatalError):
        return (
            "fatal",
            error.code,
            error.message,
            _summarize_value(error.details),
        )
    if isinstance(error, RuntimeInteractionError):
        failure_kind = (
            "cancelled"
            if error.run_state == AgentRunState.CANCELLED
            else "blocked"
        )
        return (
            failure_kind,
            error.code,
            error.message,
            _summarize_value(error.details),
        )
    if isinstance(error, ToolException):
        code = str(getattr(error, "code", "TOOL_ERROR"))
        message = str(getattr(error, "message", None) or error or "工具执行失败")
        details = getattr(error, "details", {})
        return "recoverable", code, _safe_text(message) or "工具执行失败", _summarize_value(details)

    error_name = type(error).__name__ if error is not None else "ToolError"
    message = _safe_text(error) or "未知工具异常"
    # 未知底层异常只保留类型，避免把底层解析/驱动信息发送给前端。
    return (
        "fatal",
        "RUNTIME_TOOL_EXECUTION_FAILED",
        "工具执行失败，请稍后重试。",
        {"exception_type": error_name},
    )


class RuntimeObserver:
    """观察 LangChain 原始事件，并生成不影响业务链路的 Runtime 事件。

    观察器只维护当前请求内的内存事件列表，并通过可选 sink 写结构化日志或
    其它旁路消费者；它不负责改变 Agent 的工具返回和 SSE 对外协议。
    """

    def __init__(
        self,
        context: RuntimeContext,
        *,
        event_sink: EventSink | None = None,
        result_normalizer: ResultNormalizer | None = None,
        invocation_bridge: RuntimeInvocationBridge | None = None,
    ) -> None:
        self.context = context
        self.events: list[AgentEvent] = []
        self._event_sink = event_sink
        self._result_normalizer = result_normalizer or ResultNormalizer()
        self._invocation_bridge = (
            invocation_bridge
            or get_runtime_invocation_bridge(required=False)
            or RuntimeInvocationBridge()
        )
        self._pending_calls: dict[str, ToolInvocation] = {}
        self._finished = False

    def _publish(
        self,
        event_type: AgentEventType,
        *,
        call_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            run_id=self.context.run_id,
            call_id=call_id,
            event_type=event_type,
            payload=payload or {},
            created_at=_utc_now(),
        )
        self.events.append(event)
        if self._event_sink is not None:
            try:
                self._event_sink(event)
            except Exception:
                # 旁路日志/消费失败不能影响真实 Agent 任务。
                logger.exception("Runtime 事件旁路发布失败: event_id=%s", event.event_id)
        return event

    def start(self) -> AgentEvent:
        """发布一次运行开始事件。"""

        if self._finished:
            return self.events[-1]
        self.context.state = AgentRunState.RUNNING
        return self._publish(
            AgentEventType.RUN_STARTED,
            payload={"state": self.context.state.value},
        )

    def observe(self, raw_event: dict[str, Any]) -> AgentEvent | None:
        """安全观察一条 LangChain 事件；观察器自身异常会被吞并记录。"""

        try:
            return self._observe(raw_event)
        except Exception:
            # Runtime 处于旁路模式时，不能因为观测代码破坏原执行结果。
            logger.exception("Runtime 旁路观察失败")
            return None

    def _observe(self, raw_event: dict[str, Any]) -> AgentEvent | None:
        if not isinstance(raw_event, dict):
            return None
        event_name = str(raw_event.get("event", ""))
        source_run_id = str(raw_event.get("run_id")) if raw_event.get("run_id") else None
        data = raw_event.get("data") if isinstance(raw_event.get("data"), dict) else {}
        tool_name = str(raw_event.get("name") or "unknown_tool")

        if event_name == "on_tool_start":
            argument_summary = _summarize_arguments(data.get("input", {}))
            if source_run_id:
                invocation = self._invocation_bridge.get_or_create(
                    source_run_id,
                    context=self.context,
                    tool_name=tool_name,
                    arguments=argument_summary,
                )
            else:
                invocation = ToolInvocation(
                    run_id=self.context.run_id,
                    tool_name=tool_name,
                    arguments=argument_summary,
                    started_at=_utc_now(),
                )
            self.context.call_count += 1
            if source_run_id:
                self._pending_calls[source_run_id] = invocation
            return self._publish(
                AgentEventType.TOOL_STARTED,
                call_id=invocation.call_id,
                payload={
                    "tool_name": tool_name,
                    # 事件始终使用旁路摘要；Engine 后续可能把共享 Invocation
                    # 中的 arguments 更新为供 Policy 使用的原始结构。
                    "arguments": argument_summary,
                },
            )

        if event_name == "on_tool_end":
            invocation = self._take_invocation(source_run_id, tool_name)
            raw_output = data.get("output")
            # RuntimeEngine 已完成治理时优先复用 after Hook 处理后的结果；
            # 未接入 RuntimeManagedTool 的普通工具仍由 Observer 现场标准化。
            result = invocation.result or self._result_normalizer.normalize(
                raw_output
            )
            invocation.finished_at = invocation.finished_at or _utc_now()
            invocation.result = result
            return self._publish(
                AgentEventType.TOOL_FINISHED,
                call_id=invocation.call_id,
                payload={
                    "tool_name": invocation.tool_name,
                    "result": result.model_dump(mode="json"),
                    "output_summary": self._result_normalizer.summarize(
                        raw_output
                    ),
                },
            )

        if event_name == "on_tool_error":
            invocation = self._take_invocation(source_run_id, tool_name)
            error = data.get("error") or raw_event.get("error")
            failure_kind, code, message, details = _failure_info(error)
            result = None
            if failure_kind == "recoverable":
                result = ToolResult(
                    status=ToolResultStatus.RECOVERABLE_ERROR,
                    code=code,
                    message=message,
                    retryable=True,
                    data=details,
                )
                invocation.result = result
            invocation.finished_at = _utc_now()
            return self._publish(
                AgentEventType.TOOL_FAILED,
                call_id=invocation.call_id,
                payload={
                    "tool_name": invocation.tool_name,
                    "failure_kind": failure_kind,
                    "code": code,
                    "message": message,
                    "details": details,
                    "result": result.model_dump(mode="json") if result else None,
                },
            )

        if event_name == "on_chain_error":
            error = data.get("error") or raw_event.get("error")
            return self.finish(error=error)

        return None

    def _take_invocation(self, source_run_id: str | None, tool_name: str) -> ToolInvocation:
        if source_run_id and source_run_id in self._pending_calls:
            invocation = self._pending_calls.pop(source_run_id)
            self._invocation_bridge.pop(source_run_id)
            return invocation
        if source_run_id:
            invocation = self._invocation_bridge.pop(source_run_id)
            if invocation is not None:
                return invocation

        # 极端情况下只收到结束事件，也要为真实观察到的调用补一个 call_id。
        self.context.call_count += 1
        return ToolInvocation(
            run_id=self.context.run_id,
            tool_name=tool_name,
            started_at=_utc_now(),
        )

    def finish(
        self,
        *,
        success: bool = False,
        error: Any = None,
        cancelled: bool = False,
    ) -> AgentEvent | None:
        """发布运行结束事件，并保证同一运行只结束一次。"""

        if self._finished:
            return None
        self._finished = True

        if cancelled:
            self.context.state = AgentRunState.CANCELLED
            return self._publish(
                AgentEventType.RUN_CANCELLED,
                payload={"state": self.context.state.value},
            )
        if isinstance(error, RuntimeInteractionError):
            self.context.state = error.run_state
            failure_kind, code, message, details = _failure_info(error)
            event_type = (
                AgentEventType.RUN_CANCELLED
                if error.run_state == AgentRunState.CANCELLED
                else AgentEventType.RUN_FAILED
            )
            return self._publish(
                event_type,
                call_id=error.call_id,
                payload={
                    "state": self.context.state.value,
                    "failure_kind": failure_kind,
                    "code": code,
                    "message": message,
                    "details": details,
                },
            )
        if success:
            self.context.state = AgentRunState.SUCCEEDED
            return self._publish(
                AgentEventType.RUN_FINISHED,
                payload={"state": self.context.state.value},
            )

        self.context.state = AgentRunState.FAILED
        failure_kind, code, message, details = _failure_info(error)
        return self._publish(
            AgentEventType.RUN_FAILED,
            payload={
                "state": self.context.state.value,
                "failure_kind": failure_kind,
                "code": code,
                "message": message,
                "details": details,
            },
        )
