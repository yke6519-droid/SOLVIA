"""SOLVIA Runtime 的第一版公共数据模型。

这些模型只描述 Runtime 的公共语言，不负责执行工具、访问数据库或持久化
任务状态。R1-A 先把后续 Policy、事件和工具 Wrapper 需要的边界固定下来。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.app.runtime.enums import (
    AgentEventType,
    AgentRunState,
    IntentSource,
    InteractionIntent,
    InteractionState,
    InteractionType,
    PolicyAction,
    RuntimeInteractionCode,
    ToolResultStatus,
)


def _utc_now() -> datetime:
    """统一生成带时区的 UTC 时间，避免不同模块混用本地时间。"""

    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    """生成可读且足够唯一的 Runtime 标识。"""

    return f"{prefix}_{uuid4().hex}"


class RuntimeModel(BaseModel):
    """Runtime 模型公共配置：拒绝未声明字段，避免协议悄悄漂移。"""

    model_config = ConfigDict(extra="forbid")


class ToolSpec(RuntimeModel):
    """Runtime 看到的工具描述，不复制业务 Tool 的实现。

    R2 开始使用这些执行属性做最小纵切检查；默认值保持与现有工具行为兼容，
    不在这里直接实现确认、重试或持久化。
    """

    name: str = Field(min_length=1)
    description: str = ""
    # 保存 JSON Schema 摘要，供 Runtime Policy 和审计使用；具体参数校验仍由原 Tool 执行。
    args_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_attempts: int = Field(default=1, ge=1)
    idempotent: bool = True
    side_effect_level: str = Field(default="read", min_length=1)
    requires_confirmation: bool = False
    allowed_states: list[AgentRunState] = Field(
        default_factory=lambda: [AgentRunState.RUNNING]
    )
    evidence_type: str | None = None
    checkpoint_mode: str = Field(default="none", min_length=1)


class ToolResult(RuntimeModel):
    """工具可预期结果的统一外壳。"""

    status: ToolResultStatus
    code: str | None = None
    message: str | None = None
    # R1-A 只定义承载位置；旁路观察时应写摘要而不是完整原始数据。
    data: Any | None = None
    retryable: bool = False
    suggested_actions: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class PolicyDecision(RuntimeModel):
    """Runtime 对一次调用作出的策略决定。"""

    action: PolicyAction
    reason: str
    policy_name: str


class ToolInvocation(RuntimeModel):
    """一次工具调用的统一信封。"""

    call_id: str = Field(default_factory=lambda: _new_id("call"), min_length=1)
    run_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    # 这里保留调用参数的结构位置；R1-B 旁路记录时会先做摘要和脱敏。
    arguments: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_utc_now)
    finished_at: datetime | None = None
    result: ToolResult | None = None
    policy_decision: PolicyDecision | None = None


class PendingInteraction(RuntimeModel):
    """Runtime 当前挂起、等待用户处理的一次交互。"""

    interaction_id: str = Field(
        default_factory=lambda: _new_id("interaction"),
        min_length=1,
    )
    run_id: str = Field(min_length=1)
    # 确认通常发生在某个工具调用前，因此绑定对应 call_id 便于恢复和审计。
    call_id: str | None = None
    interaction_type: InteractionType = InteractionType.CONFIRMATION
    allowed_intents: list[InteractionIntent] = Field(
        default_factory=lambda: [
            InteractionIntent.CONFIRM,
            InteractionIntent.CANCEL,
        ]
    )
    # 只有明确列出的工具参数允许被用户意图修改，默认不允许修改任何参数。
    editable_fields: list[str] = Field(default_factory=list)
    # 只保存业务指纹，不把站点名、日期等原始参数拼进 ID，避免泄露敏感输入。
    confirmation_key: str = Field(min_length=1)
    question: str = Field(min_length=1)
    status: InteractionState = InteractionState.PENDING
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime | None = None
    resolved_at: datetime | None = None
    response: str | None = None
    resolved_intent: InteractionIntent | None = None
    intent_confidence: float | None = Field(default=None, ge=0, le=1)
    intent_source: IntentSource | None = None
    # 只保存解释器给出的简短原因；日志不会记录用户原始回复或完整 slots。
    intent_reason: str = ""
    intent_slots: dict[str, Any] = Field(default_factory=dict)


class InteractionResolution(RuntimeModel):
    """一次用户回复被 Runtime 分类后的稳定结果。"""

    interaction_id: str = Field(min_length=1)
    state: InteractionState
    code: RuntimeInteractionCode | None = None
    response: str | None = None
    intent: InteractionIntent = InteractionIntent.UNCLEAR
    confidence: float = Field(default=0, ge=0, le=1)
    source: IntentSource = IntentSource.FALLBACK
    reason: str = ""
    slots: dict[str, Any] = Field(default_factory=dict)


class ConfirmationRequest(RuntimeModel):
    """ConfirmationHook 发给交互服务的一次确认请求。"""

    confirmation_key: str = Field(min_length=1)
    question: str = Field(min_length=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    allowed_intents: list[InteractionIntent] = Field(
        default_factory=lambda: [
            InteractionIntent.CONFIRM,
            InteractionIntent.CANCEL,
        ]
    )
    # 交互层的最小参数修改白名单，避免 LLM 直接修改任意工具字段。
    editable_fields: list[str] = Field(default_factory=list)


class ArgumentPatch(RuntimeModel):
    """一次经过 Runtime 校验的工具参数更新。"""

    updates: dict[str, Any] = Field(default_factory=dict)


class UserIntent(RuntimeModel):
    """解释器对用户回复做出的结构化判断。

    这个模型只描述“用户想表达什么”，不直接决定工具是否执行。
    是否允许改变 Runtime 状态，仍然由 InteractionPolicy 负责。
    """

    intent: InteractionIntent = InteractionIntent.UNCLEAR
    confidence: float = Field(default=0, ge=0, le=1)
    source: IntentSource = IntentSource.FALLBACK
    slots: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class RuntimeContext(RuntimeModel):
    """一次 Agent 运行共享的 Runtime 上下文。"""

    run_id: str = Field(default_factory=lambda: _new_id("run"), min_length=1)
    user_id: int
    session_id: str = Field(min_length=1)
    state: AgentRunState = AgentRunState.CREATED
    started_at: datetime = Field(default_factory=_utc_now)
    call_count: int = Field(default=0, ge=0)
    # call_count 由 R1 旁路观察统计；R2 单独统计已通过 Runtime 检查的调用，
    # 避免“观察一次”和“预留一次”互相重复计算调用预算。
    managed_call_count: int = Field(default=0, ge=0)
    # 当前一次运行最多挂起一个交互；R3 后续会由交互服务负责推进它的状态。
    pending_interaction: PendingInteraction | None = None
    # 已结束的交互保留在当前运行上下文中，用于同一确认键去重和审计。
    interaction_history: list[PendingInteraction] = Field(default_factory=list)
    # evidence 只保存结构化证据引用或摘要，不在此模型中持久化业务数据。
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class AgentEvent(RuntimeModel):
    """Runtime 内部统一事件；前端仍由 SSE Adapter 使用旧事件名。"""

    event_id: str = Field(default_factory=lambda: _new_id("event"), min_length=1)
    run_id: str = Field(min_length=1)
    call_id: str | None = None
    event_type: AgentEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
