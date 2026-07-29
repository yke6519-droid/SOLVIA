"""R2 RuntimeManagedTool：把 Runtime 接入真实工具执行入口。"""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from backend.app.runtime.context import get_runtime_context
from backend.app.runtime.enums import AgentRunState, PolicyAction
from backend.app.runtime.exceptions import RuntimeFatalError
from backend.app.runtime.models import (
    PolicyDecision,
    RuntimeContext,
    ToolSpec,
)


class RuntimePolicyEvaluator(Protocol):
    """R2 使用的最小策略评估接口。"""

    def __call__(
        self,
        context: RuntimeContext,
        spec: ToolSpec,
        arguments: dict[str, Any],
    ) -> PolicyDecision:
        """根据当前运行上下文返回一次策略决定。"""


def _schema_snapshot(args_schema: Any) -> dict[str, Any]:
    """读取 LangChain 参数模型的 JSON Schema，供 ToolSpec 使用。"""

    if args_schema is None:
        return {}
    if hasattr(args_schema, "model_json_schema"):
        schema = args_schema.model_json_schema()
    elif hasattr(args_schema, "schema"):
        # 兼容少数仍暴露 Pydantic v1 schema 方法的旧工具。
        schema = args_schema.schema()
    else:
        return {}
    return schema if isinstance(schema, dict) else {}


def build_tool_spec(
    tool: BaseTool,
    *,
    timeout_seconds: float | None = None,
    max_attempts: int = 1,
    idempotent: bool = True,
    side_effect_level: str = "read",
    requires_confirmation: bool = False,
    allowed_states: list[AgentRunState] | None = None,
    evidence_type: str | None = None,
    checkpoint_mode: str = "none",
) -> ToolSpec:
    """从现有 LangChain Tool 生成 Runtime ToolSpec，不复制业务实现。"""

    return ToolSpec(
        name=tool.name,
        description=tool.description or "",
        args_schema=_schema_snapshot(tool.args_schema),
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        idempotent=idempotent,
        side_effect_level=side_effect_level,
        requires_confirmation=requires_confirmation,
        allowed_states=allowed_states or [AgentRunState.RUNNING],
        evidence_type=evidence_type,
        checkpoint_mode=checkpoint_mode,
    )


def _default_allow_policy(
    _context: RuntimeContext,
    _spec: ToolSpec,
    _arguments: dict[str, Any],
) -> PolicyDecision:
    """R2 默认策略：只读工具在基础检查通过后允许执行。"""

    return PolicyDecision(
        action=PolicyAction.ALLOW,
        reason="R2 默认策略允许只读工具执行",
        policy_name="r2_default_allow",
    )


class RuntimeEngine:
    """R2 的最小执行控制器。

    当前只负责执行前状态、调用预算和基础 Policy 检查，然后把调用交给
    原始 LangChain Tool。它不复制业务代码，也不在 R2 迁移用户确认逻辑。
    """

    def __init__(
        self,
        *,
        max_tool_calls: int = 20,
        policy_evaluator: RuntimePolicyEvaluator | None = None,
    ) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls 必须大于等于 1")
        self.max_tool_calls = max_tool_calls
        self.policy_evaluator = policy_evaluator or _default_allow_policy

    @staticmethod
    def _arguments(value: Any) -> dict[str, Any]:
        """将工具输入转换成策略层可读取的对象摘要位置。"""

        if isinstance(value, dict):
            return value
        return {"value": value}

    def check(
        self,
        spec: ToolSpec,
        arguments: dict[str, Any],
    ) -> PolicyDecision:
        """执行工具前完成 R2 的确定性检查，并预留一次调用预算。"""

        context = get_runtime_context()
        if context.state not in spec.allowed_states:
            raise RuntimeFatalError(
                "RUNTIME_TOOL_STATE_NOT_ALLOWED",
                f"工具 {spec.name} 不允许在 {context.state.value} 状态执行",
                details={
                    "tool_name": spec.name,
                    "state": context.state.value,
                    "allowed_states": [state.value for state in spec.allowed_states],
                },
                run_id=context.run_id,
            )

        if context.managed_call_count >= self.max_tool_calls:
            raise RuntimeFatalError(
                "RUNTIME_TOOL_BUDGET_EXCEEDED",
                f"Runtime 工具调用预算已用尽: {self.max_tool_calls}",
                details={
                    "tool_name": spec.name,
                    "max_tool_calls": self.max_tool_calls,
                    "managed_call_count": context.managed_call_count,
                },
                run_id=context.run_id,
            )

        if spec.requires_confirmation:
            raise RuntimeFatalError(
                "RUNTIME_CONFIRMATION_NOT_IMPLEMENTED",
                f"工具 {spec.name} 需要用户确认，当前 R2 尚未接入确认状态机",
                details={"tool_name": spec.name},
                run_id=context.run_id,
            )

        decision = self.policy_evaluator(context, spec, arguments)
        if decision.action != PolicyAction.ALLOW:
            raise RuntimeFatalError(
                "RUNTIME_POLICY_DENIED",
                f"Runtime Policy 不允许执行工具 {spec.name}",
                details={
                    "tool_name": spec.name,
                    "action": decision.action.value,
                    "policy_name": decision.policy_name,
                    "reason": decision.reason,
                },
                run_id=context.run_id,
            )

        # 预算按“通过 Runtime 检查并准备执行”计数；底层工具失败也不应无限重放。
        context.managed_call_count += 1
        return decision

    def execute(
        self,
        tool: BaseTool,
        spec: ToolSpec,
        tool_input: Any,
    ) -> Any:
        """同步执行一次原始工具，关闭嵌套回调以避免 R1 旁路重复记账。"""

        # 非流式兼容接口当前尚未绑定 RuntimeContext；保留原工具行为，
        # 等后续明确非流式接口策略后再统一接入 Runtime 控制。
        if get_runtime_context(required=False) is None:
            return tool.invoke(tool_input, config={"callbacks": []})
        self.check(spec, self._arguments(tool_input))
        return tool.invoke(tool_input, config={"callbacks": []})

    async def aexecute(
        self,
        tool: BaseTool,
        spec: ToolSpec,
        tool_input: Any,
    ) -> Any:
        """异步执行一次原始工具，保持 LangChain 的异步调用语义。"""

        # 与同步入口保持一致，避免 R2 改造影响尚未接入 Runtime 的调用方。
        if get_runtime_context(required=False) is None:
            return await tool.ainvoke(tool_input, config={"callbacks": []})
        self.check(spec, self._arguments(tool_input))
        return await tool.ainvoke(tool_input, config={"callbacks": []})


class RuntimeManagedTool(BaseTool):
    """保持原 Tool 外观不变、由 RuntimeEngine 管理执行的 Wrapper。

    Wrapper 仍把原始结果返回给 Agent；流式主链路中的 RuntimeObserver 继续
    将外层 tool_end/tool_error 标准化为 ToolResult，避免 R2 改变 Agent 看到的业务文本。
    """

    _delegate: BaseTool = PrivateAttr()
    _runtime_engine: RuntimeEngine = PrivateAttr()
    _runtime_spec: ToolSpec = PrivateAttr()

    def __init__(
        self,
        delegate: BaseTool,
        *,
        runtime_engine: RuntimeEngine | None = None,
        runtime_spec: ToolSpec | None = None,
    ) -> None:
        """复制工具元数据，不复制工具函数和业务逻辑。"""

        super().__init__(
            name=delegate.name,
            description=delegate.description or "",
            args_schema=delegate.args_schema,
            return_direct=delegate.return_direct,
            verbose=delegate.verbose,
            callbacks=delegate.callbacks,
            tags=delegate.tags,
            metadata=delegate.metadata,
            handle_tool_error=delegate.handle_tool_error,
            handle_validation_error=delegate.handle_validation_error,
            response_format=delegate.response_format,
        )
        self._delegate = delegate
        self._runtime_engine = runtime_engine or RuntimeEngine()
        self._runtime_spec = runtime_spec or build_tool_spec(delegate)

    @property
    def delegate(self) -> BaseTool:
        """返回被管理的原始工具，便于测试和后续注册表检查。"""

        return self._delegate

    @property
    def runtime_spec(self) -> ToolSpec:
        """返回当前 Wrapper 使用的 Runtime 工具描述。"""

        return self._runtime_spec

    @staticmethod
    def _tool_input(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        """把 BaseTool 已解析的参数恢复成原工具可接受的输入形态。"""

        if kwargs:
            return kwargs
        if len(args) == 1:
            return args[0]
        return list(args)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步入口：只调用 RuntimeEngine，不在 Wrapper 中复制业务实现。"""

        return self._runtime_engine.execute(
            self._delegate,
            self._runtime_spec,
            self._tool_input(args, kwargs),
        )

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        """异步入口：与同步入口使用同一套 Runtime 检查。"""

        return await self._runtime_engine.aexecute(
            self._delegate,
            self._runtime_spec,
            self._tool_input(args, kwargs),
        )


def wrap_tool(
    tool: BaseTool,
    *,
    runtime_engine: RuntimeEngine | None = None,
    runtime_spec: ToolSpec | None = None,
) -> RuntimeManagedTool:
    """提供函数式包装入口，便于工具注册表逐步迁移。"""

    return RuntimeManagedTool(
        tool,
        runtime_engine=runtime_engine,
        runtime_spec=runtime_spec,
    )
