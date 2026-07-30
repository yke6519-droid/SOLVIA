"""SOLVIA Runtime 使用的状态、策略和事件枚举。"""

from enum import Enum


class AgentRunState(str, Enum):
    """一次 Agent 运行在 Runtime 中可能处于的状态。"""

    CREATED = "created"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolResultStatus(str, Enum):
    """可预期的工具结果分类；未知程序错误不应伪装成这些状态。"""

    SUCCESS = "success"
    NO_DATA = "no_data"
    RECOVERABLE_ERROR = "recoverable_error"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class PolicyAction(str, Enum):
    """Runtime 策略未来可以对工具调用采取的动作。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK_USER = "ask_user"
    RETRY = "retry"
    CANCEL = "cancel"


class InteractionType(str, Enum):
    """Runtime 当前支持的人机交互类型。"""

    CONFIRMATION = "confirmation"
    SELECTION = "selection"
    DATA_COLLECTION = "data_collection"
    MODIFICATION = "modification"


class InteractionIntent(str, Enum):
    """用户回复表达的通用交互意图。"""

    CONFIRM = "confirm"
    CANCEL = "cancel"
    MODIFY = "modify"
    SELECT = "select"
    PROVIDE = "provide"
    UNCLEAR = "unclear"


class IntentSource(str, Enum):
    """意图的判定来源，便于审计和后续调试。"""

    DETERMINISTIC = "deterministic"
    LLM = "llm"
    FALLBACK = "fallback"


class InteractionState(str, Enum):
    """一次待处理交互的生命周期状态。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"


class RuntimeInteractionCode(str, Enum):
    """R3 交互边界使用的稳定 code。"""

    CONFIRMATION_REQUIRED = "RUNTIME_CONFIRMATION_REQUIRED"
    INTERACTION_CANCELLED = "RUNTIME_INTERACTION_CANCELLED"
    INTERACTION_TIMEOUT = "RUNTIME_INTERACTION_TIMEOUT"
    INVALID_RESPONSE = "RUNTIME_INTERACTION_INVALID_RESPONSE"


class AgentEventType(str, Enum):
    """Runtime 内部事件类型；外部 SSE 名称由适配器负责兼容。"""

    RUN_STARTED = "run_started"
    INTERACTION_REQUESTED = "interaction_requested"
    RUN_WAITING_USER = "run_waiting_user"
    RUN_RESUMED = "run_resumed"
    INTERACTION_EXPIRED = "interaction_expired"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    TOOL_FAILED = "tool_failed"
    RUN_FINISHED = "run_finished"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
