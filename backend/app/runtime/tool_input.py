"""所有 LangChain 工具共用的输入边界处理。"""

from __future__ import annotations

import contextvars
import json
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from backend.app.errors import ToolError


_validation_attempts: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "runtime_tool_validation_attempts",
    default=None,
)
_tool_error_attempts: contextvars.ContextVar[dict[tuple[str, str], int] | None] = contextvars.ContextVar(
    "runtime_tool_error_attempts",
    default=None,
)


def _schema_accepts_array(schema: dict[str, Any]) -> bool:
    """判断字段 Schema 是否允许数组，兼容 Pydantic 的 anyOf 包装。"""

    if schema.get("type") == "array":
        return True
    return any(
        isinstance(item, dict) and _schema_accepts_array(item)
        for item in schema.get("anyOf", [])
    )


def _schema_properties(args_schema: Any) -> dict[str, Any]:
    """读取工具参数 Schema；读取失败时保持原始校验行为。"""

    if args_schema is None:
        return {}
    try:
        if hasattr(args_schema, "model_json_schema"):
            schema = args_schema.model_json_schema()
        else:
            schema = args_schema.schema()
    except Exception:
        return {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    return properties if isinstance(properties, dict) else {}


def normalize_tool_input(args_schema: Any, tool_input: Any) -> Any:
    """只修复明确安全的“JSON 字符串数组”，不猜测业务值。"""

    if not isinstance(tool_input, dict):
        return tool_input

    properties = _schema_properties(args_schema)
    normalized = dict(tool_input)
    for field_name, value in tool_input.items():
        field_schema = properties.get(field_name)
        if not isinstance(value, str) or not isinstance(field_schema, dict):
            continue
        if not _schema_accepts_array(field_schema):
            continue
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, list):
            normalized[field_name] = parsed
    return normalized


def _validation_details(error: Exception) -> list[dict[str, str]]:
    """提取可交给 Agent 的字段错误，不把原始输入值回传出去。"""

    details = []
    for item in getattr(error, "errors", lambda: [])():
        location = ".".join(str(part) for part in item.get("loc", ())) or "input"
        details.append(
            {
                "field": location,
                "type": str(item.get("type", "validation_error")),
                "message": str(item.get("msg", "参数校验失败")),
            }
        )
    return details


def _validation_payload(tool_name: str, error: Exception, attempt: int) -> dict[str, Any]:
    """生成统一的 Agent-facing 参数错误协议。"""

    return {
        "status": "recoverable_error",
        "code": "TOOL_ARGUMENT_INVALID",
        "message": f"工具 {tool_name} 的参数格式不正确，请按照字段错误修正后重试一次。",
        "retryable": attempt < 2,
        "data": {
            "tool_name": tool_name,
            "attempt": attempt,
            "max_correction_attempts": 1,
            "fields": _validation_details(error),
        },
    }


def build_validation_error_handler(tool_name: str):
    """构造所有工具共用的 Pydantic 错误处理器。"""

    def handle(error: Exception) -> str:
        attempts = _validation_attempts.get()
        if attempts is None:
            attempts = {}
            _validation_attempts.set(attempts)
        attempt = attempts.get(tool_name, 0) + 1
        attempts[tool_name] = attempt
        payload = _validation_payload(tool_name, error, attempt)
        if attempt > 1:
            # 第二次仍然失败时中断 Agent，避免模型在同一参数错误上无限循环。
            raise ToolError(
                "TOOL_ARGUMENT_INVALID",
                payload["message"],
                details=payload["data"],
                retryable=False,
            )
        return json.dumps(payload, ensure_ascii=False)

    return handle


def build_tool_error_handler(tool_name: str):
    """把业务 ToolError 转成 Agent 可理解的结构化工具结果。

    RuntimeFatalError 不继承 ToolException，不会进入这里；因此 Policy、
    确认状态机和未知系统异常仍然保持直接中断。
    """

    def handle(error: Exception) -> str:
        code = str(getattr(error, "code", None) or "TOOL_ERROR")
        message = str(
            getattr(error, "message", None)
            or error
            or f"工具 {tool_name} 执行失败。"
        )
        details = getattr(error, "details", {}) or {}
        data = dict(details) if isinstance(details, dict) else {}
        data.setdefault("tool_name", tool_name)
        requested_retryable = bool(getattr(error, "retryable", False))
        retryable = requested_retryable
        if requested_retryable:
            attempts = _tool_error_attempts.get()
            if attempts is None:
                attempts = {}
                _tool_error_attempts.set(attempts)
            attempt_key = (tool_name, code)
            attempt = attempts.get(attempt_key, 0) + 1
            attempts[attempt_key] = attempt
            data.setdefault("attempt", attempt)
            data.setdefault("max_correction_attempts", 1)
            retryable = attempt <= 1
        payload = {
            "status": (
                "blocked"
                if code == "STATION_SELECTION_REQUIRED"
                else "recoverable_error"
            ),
            "code": code,
            "message": message,
            "retryable": retryable,
            "data": data,
            "suggested_actions": [],
            "artifact_ids": [],
        }
        return json.dumps(payload, ensure_ascii=False)

    return handle


def reset_tool_input_retry_state() -> None:
    """清理当前执行上下文的参数纠错次数，供测试和请求边界使用。"""

    _validation_attempts.set(None)
    _tool_error_attempts.set(None)


class ToolInputBoundary(BaseTool):
    """保持原工具名称和 Schema，只接管统一的输入修复与错误反馈。"""

    _delegate: BaseTool = PrivateAttr()

    def __init__(self, delegate: BaseTool) -> None:
        super().__init__(
            name=delegate.name,
            description=delegate.description or "",
            args_schema=delegate.args_schema,
            return_direct=delegate.return_direct,
            verbose=delegate.verbose,
            callbacks=delegate.callbacks,
            tags=delegate.tags,
            metadata=delegate.metadata,
            handle_tool_error=build_tool_error_handler(delegate.name),
            handle_validation_error=build_validation_error_handler(delegate.name),
            response_format=delegate.response_format,
        )
        self._delegate = delegate

    def _parse_input(self, tool_input: Any, tool_call_id: str | None) -> Any:
        """在 LangChain/Pydantic 校验前做一次通用的安全格式修复。"""

        normalized = normalize_tool_input(self.args_schema, tool_input)
        return super()._parse_input(normalized, tool_call_id)

    def _run(self, *args: Any, config: Any = None, run_manager=None, **kwargs: Any) -> Any:
        return self._delegate._run(
            *args,
            config=config,
            run_manager=run_manager,
            **kwargs,
        )

    async def _arun(
        self,
        *args: Any,
        config: Any = None,
        run_manager=None,
        **kwargs: Any,
    ) -> Any:
        return await self._delegate._arun(
            *args,
            config=config,
            run_manager=run_manager,
            **kwargs,
        )


def wrap_tool_input(tool: BaseTool) -> ToolInputBoundary:
    """创建输入边界包装器，不复制原工具业务逻辑。"""

    return ToolInputBoundary(tool)
