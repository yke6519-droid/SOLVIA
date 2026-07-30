"""RuntimeContext 的任务级 ContextVar 绑定。"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

from backend.app.runtime.enums import AgentRunState
from backend.app.runtime.exceptions import RuntimeFatalError
from backend.app.runtime.models import RuntimeContext, ToolInvocation


class RuntimeInvocationBridge:
    """在 LangChain 事件与 RuntimeEngine 之间共享本次 ToolInvocation。

    Bridge 只在当前 Agent Task 的 ContextVar 生命周期内存在，并使用
    LangChain 工具 run_id 作为临时关联键，不负责持久化 Runtime 数据。
    """

    def __init__(self) -> None:
        self._invocations: dict[str, ToolInvocation] = {}

    def get_or_create(
        self,
        source_run_id: str,
        *,
        context: RuntimeContext,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        replace_arguments: bool = False,
    ) -> ToolInvocation:
        """取得同一次调用的共享对象；Engine 可用原始参数替换事件摘要。"""

        key = str(source_run_id)
        invocation = self._invocations.get(key)
        if invocation is None:
            invocation = ToolInvocation(
                run_id=context.run_id,
                tool_name=tool_name,
                arguments=arguments or {},
            )
            self._invocations[key] = invocation
            return invocation

        if replace_arguments and arguments is not None:
            invocation.arguments = arguments
        if invocation.tool_name == "unknown_tool" and tool_name:
            invocation.tool_name = tool_name
        return invocation

    def pop(self, source_run_id: str) -> ToolInvocation | None:
        """工具结束后释放临时关联，避免一次运行内持续积累。"""

        return self._invocations.pop(str(source_run_id), None)

    def get(self, source_run_id: str) -> ToolInvocation | None:
        """只读取得当前关联，主要用于诊断和测试。"""

        return self._invocations.get(str(source_run_id))


@dataclass(frozen=True)
class RuntimeContextBinding:
    """同时保存 RuntimeContext 与 InvocationBridge 的回滚 Token。"""

    context_token: contextvars.Token
    invocation_bridge_token: contextvars.Token


_current_context: contextvars.ContextVar[RuntimeContext | None] = contextvars.ContextVar(
    "runtime_execution_context",
    default=None,
)
_current_invocation_bridge: contextvars.ContextVar[
    RuntimeInvocationBridge | None
] = contextvars.ContextVar(
    "runtime_invocation_bridge",
    default=None,
)


def bind_runtime_context(session_id: str, user_id: int):
    """在当前 Agent 执行 Task 中绑定一个新的 RuntimeContext。"""

    context = RuntimeContext(
        session_id=session_id,
        user_id=user_id,
        state=AgentRunState.CREATED,
    )
    return RuntimeContextBinding(
        context_token=_current_context.set(context),
        invocation_bridge_token=_current_invocation_bridge.set(
            RuntimeInvocationBridge()
        ),
    )


def get_runtime_context(*, required: bool = True) -> RuntimeContext | None:
    """读取当前任务的 RuntimeContext。"""

    context = _current_context.get()
    if context is None and required:
        raise RuntimeFatalError(
            "RUNTIME_CONTEXT_MISSING",
            "当前 Runtime 任务缺少执行上下文",
        )
    return context


def get_runtime_invocation_bridge(
    *,
    required: bool = True,
) -> RuntimeInvocationBridge | None:
    """读取当前任务的 InvocationBridge。"""

    bridge = _current_invocation_bridge.get()
    if bridge is None and required:
        raise RuntimeFatalError(
            "RUNTIME_INVOCATION_BRIDGE_MISSING",
            "当前 Runtime 任务缺少工具调用桥接上下文",
        )
    return bridge


def reset_runtime_context(binding: RuntimeContextBinding) -> None:
    """恢复绑定前的上下文，避免本轮状态泄漏到后续请求。"""

    # 先释放后绑定的 Bridge，再恢复 RuntimeContext，保持栈式回滚顺序。
    _current_invocation_bridge.reset(binding.invocation_bridge_token)
    _current_context.reset(binding.context_token)
