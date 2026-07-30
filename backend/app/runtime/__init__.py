"""SOLVIA Runtime 公共模型、旁路观察和 R2 执行入口。"""

from backend.app.runtime.enums import (
    AgentEventType,
    AgentRunState,
    InteractionIntent,
    InteractionState,
    InteractionType,
    IntentSource,
    PolicyAction,
    RuntimeInteractionCode,
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
from backend.app.runtime.exceptions import RuntimeFatalError, RuntimeInteractionError
from backend.app.runtime.models import (
    AgentEvent,
    ConfirmationRequest,
    InteractionResolution,
    PolicyDecision,
    PendingInteraction,
    RuntimeContext,
    ToolInvocation,
    ToolResult,
    ToolSpec,
    UserIntent,
)
from backend.app.runtime.interactions import make_confirmation_key
from backend.app.runtime.interaction_service import (
    InteractionTransport,
    RuntimeInteractionService,
)
from backend.app.runtime.intent_interpreter import (
    DeterministicIntentInterpreter,
    HybridUserIntentInterpreter,
    InteractionPolicy,
    LLMIntentInterpreter,
    UserIntentInterpreter,
    normalize_user_reply,
)
from backend.app.runtime.observer import RuntimeObserver
from backend.app.runtime.result_normalizer import ResultNormalizer
from backend.app.runtime.hooks import (
    BaseRuntimeHook,
    BudgetHook,
    ConfirmationHook,
    ConfirmationRequestBuilder,
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
    "ConfirmationHook",
    "ConfirmationRequest",
    "ConfirmationRequestBuilder",
    "bind_runtime_context",
    "get_runtime_context",
    "get_runtime_invocation_bridge",
    "InteractionState",
    "InteractionIntent",
    "InteractionResolution",
    "InteractionType",
    "InteractionTransport",
    "IntentSource",
    "DeterministicIntentInterpreter",
    "HybridUserIntentInterpreter",
    "InteractionPolicy",
    "LLMIntentInterpreter",
    "UserIntentInterpreter",
    "normalize_user_reply",
    "make_confirmation_key",
    "PendingInteraction",
    "PolicyAction",
    "PolicyDecision",
    "PolicyHook",
    "RuntimeContext",
    "RuntimeContextBinding",
    "RuntimeEngine",
    "RuntimeFatalError",
    "RuntimeInteractionError",
    "RuntimeInteractionCode",
    "RuntimeInteractionService",
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
    "UserIntent",
    "StateHook",
    "wrap_tool",
]
