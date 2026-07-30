"""R2 RuntimeManagedTool：把 Runtime 接入真实工具执行入口。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import BaseTool, ToolException
from pydantic import PrivateAttr

from backend.app.runtime.context import (
    get_runtime_context,
    get_runtime_invocation_bridge,
)
from backend.app.runtime.enums import AgentRunState, PolicyAction
from backend.app.runtime.exceptions import RuntimeFatalError
from backend.app.runtime.models import (
    PolicyDecision,
    RuntimeContext,
    ToolInvocation,
    ToolSpec,
)
from backend.app.runtime.hooks import (
    BudgetHook,
    PolicyHook,
    RuntimeHookChain,
    StateHook,
)
from backend.app.runtime.result_normalizer import ResultNormalizer


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


class RuntimeEngine:
    """R2/R2.5 的工具执行生命周期编排器。

    已迁移的状态、预算和 Policy 规则由 Hook 实现；本类只编排异步工具
    生命周期、确认边界和原始 LangChain Tool 委托，不复制业务代码。
    """

    def __init__(
        self,
        *,
        max_tool_calls: int = 20,
        result_normalizer: ResultNormalizer | None = None,
    ) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls 必须大于等于 1")
        self.max_tool_calls = max_tool_calls
        self._state_hook = StateHook()
        self._budget_hook = BudgetHook(max_tool_calls)
        self._policy_hook = PolicyHook()
        self._result_normalizer = result_normalizer or ResultNormalizer()
        # 当前默认链已经接入状态、预算和 Policy Hook；确认规则仍保留在
        # RuntimeEngine，后续再迁移，避免一次性改变 R2 的执行语义。
        self._hook_chain = RuntimeHookChain(
            [self._state_hook, self._budget_hook, self._policy_hook]
        )

    @property
    def hook_chain(self) -> RuntimeHookChain:
        """返回当前 Runtime 使用的 Hook Chain，便于后续注册新规则。"""

        return self._hook_chain

    @staticmethod
    def _arguments(value: Any) -> dict[str, Any]:
        """将工具输入转换成策略层可读取的对象摘要位置。"""

        if isinstance(value, dict):
            return value
        return {"value": value}

    @staticmethod
    def _check_confirmation(
        context: RuntimeContext,
        spec: ToolSpec,
    ) -> None:
        """保留 R2 的确认边界，等待后续 ConfirmationHook 接入。"""

        if spec.requires_confirmation:
            raise RuntimeFatalError(
                "RUNTIME_CONFIRMATION_NOT_IMPLEMENTED",
                f"工具 {spec.name} 需要用户确认，当前 R2 尚未接入确认状态机",
                details={"tool_name": spec.name},
                run_id=context.run_id,
            )

    @staticmethod
    def _build_invocation(
        context: RuntimeContext,
        spec: ToolSpec,
        arguments: dict[str, Any],
        source_run_id: str | None = None,
    ) -> ToolInvocation:
        """为 Hook 取得调用信封；有外部 run_id 时与 Observer 共享对象。"""

        if source_run_id:
            bridge = get_runtime_invocation_bridge()
            return bridge.get_or_create(
                source_run_id,
                context=context,
                tool_name=spec.name,
                arguments=arguments,
                replace_arguments=True,
            )
        return ToolInvocation(
            run_id=context.run_id,
            tool_name=spec.name,
            arguments=arguments,
        )

    @staticmethod
    def _raise_for_hook_decision(
        context: RuntimeContext,
        spec: ToolSpec,
        decision: PolicyDecision,
        *,
        call_id: str | None = None,
    ) -> None:
        """将 Hook 的非允许决定转换为当前 R2 的结构化 Runtime 错误。"""

        if decision.action == PolicyAction.ALLOW:
            return
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
            call_id=call_id,
        )

    async def aexecute(
        self,
        tool: BaseTool,
        spec: ToolSpec,
        tool_input: Any,
        *,
        source_run_id: str | None = None,
    ) -> Any:
        """异步执行一次原始工具，保持 LangChain 的异步调用语义。"""

        # RuntimeManagedTool 是受治理工具；缺少上下文时必须失败，不能绕过
        # Hook 直接调用原始工具。
        context = get_runtime_context()
        arguments = self._arguments(tool_input)
        invocation = self._build_invocation(
            context,
            spec,
            arguments,
            source_run_id,
        )
        # 确认尚未 Hook 化，先保持 R2 原有顺序：确认检查早于 Policy 判断。
        self._check_confirmation(context, spec)
        decision = await self._hook_chain.run_before_tool_call(
            context,
            invocation,
            spec,
        )
        invocation.policy_decision = decision
        self._raise_for_hook_decision(
            context,
            spec,
            decision,
            call_id=invocation.call_id,
        )
        # 前置 Hook 全部允许后才预留预算，确保 Policy DENY 不消耗调用次数。
        self._budget_hook.reserve(context, spec)
        try:
            raw_output = await tool.ainvoke(
                tool_input,
                config={"callbacks": []},
            )

        # Runtime 使用统一 ToolResult 进入后置 Hook；Agent 仍接收原始业务输出，
        # 避免 R2.5 结构重构改变现有工具和 Prompt 之间的返回协议。
            normalized_result = self._result_normalizer.normalize(raw_output)
            invocation.finished_at = datetime.now(timezone.utc)
            invocation.result = normalized_result
        # 更新 invocation 的 result 位置，便于后置 Hook 读取标准化结果。
            invocation.result = await self._hook_chain.run_after_tool_call(
                context,
                invocation,
                spec,
                normalized_result,
            )
            return raw_output
        except (RuntimeFatalError, ToolException):
            # 已经具备 Runtime/Tool 协议的异常不能二次包装，保留原始错误类型。
            raise
        except Exception as exc:
            # 受 Runtime 管理的工具出现未知异常时，只向上暴露稳定的 Runtime 错误协议。
            raise RuntimeFatalError(
                "RUNTIME_TOOL_EXECUTION_FAILED",
                f"工具 {spec.name} 执行失败。",
                details={
                    "tool_name": spec.name,
                    "exception_type": type(exc).__name__,
                },
                run_id=context.run_id,
                call_id=invocation.call_id,
            ) from exc


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
        """BaseTool 要求的同步扩展点；受治理工具只允许异步执行。"""

        del args, kwargs
        context = get_runtime_context(required=False)
        raise RuntimeFatalError(
            "RUNTIME_SYNC_EXECUTION_NOT_SUPPORTED",
            f"Runtime 管理工具 {self.name} 只支持异步执行",
            details={"tool_name": self.name},
            run_id=context.run_id if context is not None else None,
        )

    async def _arun(
        self,
        *args: Any,
        run_manager=None,
        **kwargs: Any,
    ) -> Any:
        """异步入口：携带 LangChain run_id 进入 Runtime 生命周期。"""

        return await self._runtime_engine.aexecute(
            self._delegate,
            self._runtime_spec,
            self._tool_input(args, kwargs),
            source_run_id=(
                str(run_manager.run_id)
                if run_manager is not None and run_manager.run_id is not None
                else None
            ),
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
