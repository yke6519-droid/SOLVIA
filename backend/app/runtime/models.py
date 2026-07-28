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
    PolicyAction,
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
    """Runtime 看到的工具描述，不复制业务 Tool 的实现。"""

    name: str = Field(min_length=1)
    description: str = ""
    # 保存 JSON Schema 摘要，供未来 Policy/审计使用；R1-A 不执行校验。
    args_schema: dict[str, Any] = Field(default_factory=dict)


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


class RuntimeContext(RuntimeModel):
    """一次 Agent 运行共享的 Runtime 上下文。"""

    run_id: str = Field(default_factory=lambda: _new_id("run"), min_length=1)
    user_id: int
    session_id: str = Field(min_length=1)
    state: AgentRunState = AgentRunState.CREATED
    started_at: datetime = Field(default_factory=_utc_now)
    call_count: int = Field(default=0, ge=0)
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

