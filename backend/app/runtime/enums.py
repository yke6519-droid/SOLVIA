"""SOLVIA Runtime 使用的状态、策略和事件枚举。"""

from enum import Enum


class AgentRunState(str, Enum):
    """一次 Agent 运行在 Runtime 中可能处于的状态。"""

    CREATED = "created"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
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


class AgentEventType(str, Enum):
    """Runtime 内部事件类型；外部 SSE 名称由适配器负责兼容。"""

    RUN_STARTED = "run_started"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    TOOL_FAILED = "tool_failed"
    RUN_FINISHED = "run_finished"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
