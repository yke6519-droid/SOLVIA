"""LangChain 事件到 Runtime AgentEvent 的旁路观察器。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from langchain_core.tools import ToolException

from backend.app.runtime.enums import (
    AgentEventType,
    AgentRunState,
    ToolResultStatus,
)
from backend.app.runtime.exceptions import RuntimeFatalError
from backend.app.runtime.models import (
    AgentEvent,
    RuntimeContext,
    ToolInvocation,
    ToolResult,
)


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


def _to_mapping(value: Any) -> dict[str, Any] | None:
    """尽量读取结构化工具结果，但不把原始文本直接写入事件。"""

    if isinstance(value, dict):
        return value
    if hasattr(value, "content"):
        value = value.content
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _safe_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    return text if len(text) <= _MAX_TEXT_LENGTH else f"{text[:_MAX_TEXT_LENGTH]}..."


def _extract_artifact_ids(value: Any) -> list[str]:
    """从结构化结果提取制品 ID，不保存完整业务结果。"""

    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")][:20]
    return []


def _summarize_output(raw_output: Any) -> dict[str, Any]:
    """生成工具结果的可审计摘要。"""

    mapping = _to_mapping(raw_output)
    if mapping is None:
        return {
            "type": "text",
            "length": len(str(raw_output or "")),
        }

    summary: dict[str, Any] = {"type": "object"}
    for key in (
        "status",
        "code",
        "result_type",
        "artifact_id",
        "artifact_ids",
        "file_id",
        "chart_id",
        "row_count",
    ):
        if key in mapping and mapping[key] not in (None, ""):
            value = mapping[key]
            summary[key] = _summarize_value(value, key=key)
    error = mapping.get("error")
    if isinstance(error, dict):
        summary["error"] = {
            key: _safe_text(error.get(key))
            for key in ("code", "message")
            if error.get(key) not in (None, "")
        }
    return summary


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
    if isinstance(error, ToolException):
        code = str(getattr(error, "code", "TOOL_ERROR"))
        message = str(getattr(error, "message", None) or error or "工具执行失败")
        details = getattr(error, "details", {})
        return "recoverable", code, _safe_text(message) or "工具执行失败", _summarize_value(details)

    error_name = type(error).__name__ if error is not None else "ToolError"
    message = _safe_text(error) or "未知工具异常"
    return "fatal", f"RUNTIME_{error_name.upper()}", message, {}


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
    ) -> None:
        self.context = context
        self.events: list[AgentEvent] = []
        self._event_sink = event_sink
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
            invocation = ToolInvocation(
                run_id=self.context.run_id,
                tool_name=tool_name,
                arguments=_summarize_arguments(data.get("input", {})),
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
                    "arguments": invocation.arguments,
                },
            )

        if event_name == "on_tool_end":
            invocation = self._take_invocation(source_run_id, tool_name)
            result = self._result_from_output(data.get("output"))
            invocation.finished_at = _utc_now()
            invocation.result = result
            return self._publish(
                AgentEventType.TOOL_FINISHED,
                call_id=invocation.call_id,
                payload={
                    "tool_name": invocation.tool_name,
                    "result": result.model_dump(mode="json"),
                    "output_summary": _summarize_output(data.get("output")),
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
            return self._pending_calls.pop(source_run_id)

        # 极端情况下只收到结束事件，也要为真实观察到的调用补一个 call_id。
        self.context.call_count += 1
        return ToolInvocation(
            run_id=self.context.run_id,
            tool_name=tool_name,
            started_at=_utc_now(),
        )

    @staticmethod
    def _result_from_output(raw_output: Any) -> ToolResult:
        mapping = _to_mapping(raw_output)
        status = ToolResultStatus.SUCCESS
        code = None
        message = None
        retryable = False
        suggested_actions: list[str] = []
        artifact_ids: list[str] = []

        if mapping:
            raw_status = str(mapping.get("status", "")).lower()
            if raw_status in {item.value for item in ToolResultStatus}:
                status = ToolResultStatus(raw_status)
            elif mapping.get("error"):
                status = ToolResultStatus.RECOVERABLE_ERROR
            code = _safe_text(mapping.get("code"))
            message = _safe_text(mapping.get("message"))
            retryable = bool(mapping.get("retryable", False))
            # 图表工具等旧协议会把错误码放在 error 内部。Runtime 对外
            # 统一提升到 ToolResult.code，避免 Policy/前端继续深挖 data。
            nested_error = mapping.get("error")
            if isinstance(nested_error, dict):
                code = code or _safe_text(nested_error.get("code"))
                message = message or _safe_text(nested_error.get("message"))
                retryable = retryable or bool(nested_error.get("retryable", False))
            suggested_actions = [
                str(item) for item in mapping.get("suggested_actions", [])
                if item not in (None, "")
            ][:10]
            artifact_ids = _extract_artifact_ids(mapping.get("artifact_ids"))
            if mapping.get("artifact_id"):
                artifact_ids.extend(_extract_artifact_ids(mapping.get("artifact_id")))

        return ToolResult(
            status=status,
            code=code,
            message=message,
            retryable=retryable,
            suggested_actions=suggested_actions,
            artifact_ids=list(dict.fromkeys(artifact_ids)),
            data=_summarize_output(raw_output),
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
