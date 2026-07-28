"""SOLVIA Runtime 公共模型入口。

R1-A 只提供类型和异常定义；真正的 Agent 事件观察与工具接入放到后续阶段。
"""

from backend.app.runtime.enums import (
    AgentEventType,
    AgentRunState,
    PolicyAction,
    ToolResultStatus,
)
from backend.app.runtime.context import (
    bind_runtime_context,
    get_runtime_context,
    reset_runtime_context,
)
from backend.app.runtime.exceptions import RuntimeFatalError
from backend.app.runtime.models import (
    AgentEvent,
    PolicyDecision,
    RuntimeContext,
    ToolInvocation,
    ToolResult,
    ToolSpec,
)
from backend.app.runtime.observer import RuntimeObserver

__all__ = [
    "AgentEvent",
    "AgentEventType",
    "AgentRunState",
    "bind_runtime_context",
    "get_runtime_context",
    "PolicyAction",
    "PolicyDecision",
    "RuntimeContext",
    "RuntimeFatalError",
    "RuntimeObserver",
    "reset_runtime_context",
    "ToolInvocation",
    "ToolResult",
    "ToolResultStatus",
    "ToolSpec",
]
