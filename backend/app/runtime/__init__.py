"""SOLVIA Runtime 公共模型、旁路观察和 R2 执行入口。"""

from backend.app.runtime.enums import (
    AgentEventType,
    AgentRunState,
    PolicyAction,
    ToolResultStatus,
)
from backend.app.runtime.context import (
    RuntimeContextBinding,
    RuntimeInvocationBridge,
    bind_runtime_context,
    get_runtime_context,
    get_runtime_invocation_bridge,
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
from backend.app.runtime.result_normalizer import ResultNormalizer
from backend.app.runtime.hooks import (
    BaseRuntimeHook,
    BudgetHook,
    PolicyHook,
    RuntimeHook,
    RuntimeHookChain,
    StateHook,
)
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
    "BaseRuntimeHook",
    "BudgetHook",
    "bind_runtime_context",
    "get_runtime_context",
    "get_runtime_invocation_bridge",
    "PolicyAction",
    "PolicyDecision",
    "PolicyHook",
    "RuntimeContext",
    "RuntimeContextBinding",
    "RuntimeEngine",
    "RuntimeFatalError",
    "RuntimeHook",
    "RuntimeHookChain",
    "RuntimeInvocationBridge",
    "RuntimeManagedTool",
    "RuntimeObserver",
    "ResultNormalizer",
    "build_tool_spec",
    "reset_runtime_context",
    "ToolInvocation",
    "ToolResult",
    "ToolResultStatus",
    "ToolSpec",
    "StateHook",
    "wrap_tool",
]
