"""R2.5 Runtime Hook 契约与执行链。

本文件建立 Hook 的公共接口和执行顺序，并承载已经逐步迁移的状态、预算
和 Policy 规则。每种规则独立实现，RuntimeEngine 只负责执行生命周期编排。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol

from backend.app.runtime.enums import PolicyAction
from backend.app.runtime.exceptions import RuntimeFatalError
from backend.app.runtime.models import (
    PolicyDecision,
    RuntimeContext,
    ToolInvocation,
    ToolResult,
    ToolSpec,
)


class RuntimeHook(Protocol):
    """一次工具调用生命周期中的可插拔规则接口。

    Hook 使用异步方法，是为了与当前 Agent 的异步流式执行链保持一致。
    Runtime 管理工具已明确只支持异步治理主链，因此 Hook 不再承担同步适配。

    ``spec`` 被显式传入，Hook 不需要通过工具名称去全局查找工具配置。
    """

    async def before_tool_call(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
    ) -> PolicyDecision | None:
        """在原始工具执行前返回决定；返回 None 表示本 Hook 不发表意见。"""

    async def after_tool_call(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
        result: ToolResult,
    ) -> ToolResult:
        """在工具完成后读取或转换标准化结果。"""


class BaseRuntimeHook:
    """RuntimeHook 的默认空实现，子类只需覆盖自己关心的生命周期阶段。

    before 默认返回 None，表示“不阻止、不决策”；after 默认原样返回 result，
    表示“不修改结果”。这两个默认行为让前置 Hook 不必重复编写 after，后置 Hook
    也不必重复编写 before。
    """

    async def before_tool_call(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
    ) -> PolicyDecision | None:
        """默认不对本次工具调用发表决策。"""

        del context, invocation, spec
        return None

    async def after_tool_call(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
        result: ToolResult,
    ) -> ToolResult:
        """默认不修改工具结果，直接交给下一个 Hook。"""

        del context, invocation, spec
        return result


def _default_allow_decision() -> PolicyDecision:
    """空 Hook 链的默认结果，保证接入骨架不会改变当前 R2 语义。"""

    return PolicyDecision(
        action=PolicyAction.ALLOW,
        reason="没有 Hook 阻止本次工具调用",
        policy_name="runtime_hook_chain",
    )


class StateHook(BaseRuntimeHook):
    """R2.5 第一个真实接入的 Hook：检查工具允许的运行状态。

    状态规则原本位于 ``RuntimeEngine.check`` 中。现在规则本身放到这里，
    RuntimeEngine 只负责在异步执行入口调用它。
    """

    def validate(self, context: RuntimeContext, spec: ToolSpec) -> None:
        """执行状态检查；不满足条件时保持 R2 原有错误契约。"""

        if context.state not in spec.allowed_states:
            raise RuntimeFatalError(
                "RUNTIME_TOOL_STATE_NOT_ALLOWED",
                f"工具 {spec.name} 不允许在 {context.state.value} 状态执行",
                details={
                    "tool_name": spec.name,
                    "state": context.state.value,
                    "allowed_states": [
                        state.value for state in spec.allowed_states
                    ],
                },
                run_id=context.run_id,
            )

    async def before_tool_call(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
    ) -> PolicyDecision | None:
        """异步 Hook 入口：状态通过时不发表额外策略意见。"""

        del invocation
        self.validate(context, spec)
        return None


class BudgetHook(BaseRuntimeHook):
    """R2.5 第二个真实接入的 Hook：检查并预留工具调用预算。"""

    def __init__(self, max_tool_calls: int) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls 必须大于等于 1")
        self.max_tool_calls = max_tool_calls

    def validate(self, context: RuntimeContext, spec: ToolSpec) -> None:
        """只检查预算，不修改计数，供 Policy 判断前使用。"""

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

    def reserve(self, context: RuntimeContext, spec: ToolSpec) -> None:
        """在 Policy 已允许后预留一次预算，保持 R2 原有计数时机。"""

        self.validate(context, spec)
        context.managed_call_count += 1

    async def before_tool_call(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
    ) -> PolicyDecision | None:
        """异步 Hook 入口：先检查预算，但暂不消耗预算。"""

        del invocation
        self.validate(context, spec)
        return None


def _default_allow_policy(
    _context: RuntimeContext,
    _spec: ToolSpec,
    _arguments: dict[str, Any],
) -> PolicyDecision:
    """默认只读策略：前置规则通过后允许工具继续执行。"""

    return PolicyDecision(
        action=PolicyAction.ALLOW,
        reason="R2 默认策略允许只读工具执行",
        policy_name="r2_default_allow",
    )


class PolicyHook(BaseRuntimeHook):
    """R2.5 第三个真实接入的 Hook：执行工具调用前的 Policy 判断。"""

    def __init__(
        self,
        policy: Callable[
            [RuntimeContext, ToolSpec, dict[str, Any]],
            PolicyDecision,
        ]
        | None = None,
    ) -> None:
        """直接接收策略函数；未传入时使用当前 R2 的默认允许策略。"""

        self._policy = policy or _default_allow_policy

    async def before_tool_call(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
    ) -> PolicyDecision:
        """使用统一 ToolInvocation 参数执行策略函数，并校验返回值。"""

        decision = self._policy(context, spec, invocation.arguments)
        if not isinstance(decision, PolicyDecision):
            raise TypeError("PolicyHook 策略函数必须返回 PolicyDecision")
        return decision


class RuntimeHookChain:
    """按注册顺序执行 RuntimeHook 的编排器。

    before 阶段遵循“首个非 ALLOW 决定短路”规则；所有 Hook 都不发表意见时
    返回默认 ALLOW，有 Hook 返回 ALLOW 时保留最后一个 ALLOW 的策略元数据。
    after 阶段按相同顺序串行传递结果，后一个 Hook 可以读取前一个 Hook 返回
    的 ToolResult。
    """

    def __init__(self, hooks: Iterable[RuntimeHook] | None = None) -> None:
        self._hooks = list(hooks or [])

    @property
    def hooks(self) -> tuple[RuntimeHook, ...]:
        """返回当前 Hook 注册快照，避免调用方直接修改内部列表。"""

        return tuple(self._hooks)

    def add(self, hook: RuntimeHook) -> None:
        """按顺序注册一个 Hook。"""

        self._hooks.append(hook)

    async def run_before_tool_call(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
    ) -> PolicyDecision:
        """调度所有前置 Hook，并在拒绝或暂停时立即停止后续检查。"""

        last_allow_decision: PolicyDecision | None = None
        for hook in self._hooks:
            decision = await hook.before_tool_call(context, invocation, spec)
            if decision is None:
                continue
            if not isinstance(decision, PolicyDecision):
                raise TypeError(
                    "RuntimeHook.before_tool_call 必须返回 PolicyDecision 或 None"
                )
            if decision.action != PolicyAction.ALLOW:
                return decision
            # 保留最后一个 ALLOW 决定的 policy_name/reason，避免被默认值覆盖。
            last_allow_decision = decision

        return last_allow_decision or _default_allow_decision()

    async def run_after_tool_call(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
        result: ToolResult,
    ) -> ToolResult:
        """调度所有后置 Hook，按注册顺序传递和转换工具结果。"""

        current_result = result
        for hook in self._hooks:
            current_result = await hook.after_tool_call(
                context,
                invocation,
                spec,
                current_result,
            )
            if not isinstance(current_result, ToolResult):
                raise TypeError(
                    "RuntimeHook.after_tool_call 必须返回 ToolResult"
                )
        return current_result


__all__ = [
    "BaseRuntimeHook",
    "BudgetHook",
    "PolicyHook",
    "RuntimeHook",
    "RuntimeHookChain",
    "StateHook",
]
