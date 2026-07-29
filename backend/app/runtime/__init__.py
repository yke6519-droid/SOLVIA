"""SOLVIA Runtime 公共模型、旁路观察和 R2 执行入口。"""

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
from backend.app.runtime.managed_tool import (
    RuntimeEngine,
    RuntimeManagedTool,
    build_tool_spec,
    wrap_tool,
)

__all__ = [
    "AgentEvent",
    "AgentEventType",
    "AgentRunState",
    "bind_runtime_context",
    "get_runtime_context",
    "PolicyAction",
    "PolicyDecision",
    "RuntimeContext",
    "RuntimeEngine",
    "RuntimeFatalError",
    "RuntimeManagedTool",
    "RuntimeObserver",
    "build_tool_spec",
    "reset_runtime_context",
    "ToolInvocation",
    "ToolResult",
    "ToolResultStatus",
    "ToolSpec",
    "wrap_tool",
]
