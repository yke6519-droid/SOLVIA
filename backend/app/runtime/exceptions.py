"""Runtime 层严重异常定义。"""

from __future__ import annotations

from typing import Any

from backend.app.runtime.enums import AgentRunState


class RuntimeFatalError(RuntimeError):
    """表示 Runtime 不应交给 Agent 自行修复或绕过的严重错误。

    该异常只保存结构化错误信息，不负责决定 HTTP 状态码，也不直接改变
    当前 SSE 协议。R1-A 先提供统一基类，后续 R1-B/R4 再接入事件和错误适配。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        run_id: str | None = None,
        call_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.details = details or {}
        self.run_id = run_id
        self.call_id = call_id


class RuntimeInteractionError(RuntimeError):
    """表示确认、取消或超时导致工具没有继续执行。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        run_state: AgentRunState,
        details: dict[str, Any] | None = None,
        run_id: str | None = None,
        call_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.run_state = run_state
        self.details = details or {}
        self.run_id = run_id
        self.call_id = call_id
