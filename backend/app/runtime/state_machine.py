"""Runtime Agent 运行状态机。

状态机是 RuntimeContext.state 的唯一写入口。Observer、InteractionService
以及后续的执行协调组件只能通过这里提出状态转换，不能绕过状态机直接修改状态。
状态机本身不保存任务数据，也不负责发布事件。
"""

from __future__ import annotations

from backend.app.runtime.enums import AgentRunState
from backend.app.runtime.models import RuntimeContext
from backend.app.runtime.exceptions import RuntimeFatalError


class RuntimeStateMachine:
    """校验并执行一次 Agent 运行状态转换。"""

    _ALLOWED_TRANSITIONS: dict[AgentRunState, frozenset[AgentRunState]] = {
        AgentRunState.CREATED: frozenset(
            {
                AgentRunState.RUNNING,
                AgentRunState.FAILED,
                AgentRunState.CANCELLED,
            }
        ),
        AgentRunState.RUNNING: frozenset(
            {
                AgentRunState.WAITING_USER,
                AgentRunState.BLOCKED,
                AgentRunState.SUCCEEDED,
                AgentRunState.FAILED,
                AgentRunState.CANCELLED,
            }
        ),
        AgentRunState.WAITING_USER: frozenset(
            {
                AgentRunState.RUNNING,
                AgentRunState.BLOCKED,
                AgentRunState.FAILED,
                AgentRunState.CANCELLED,
            }
        ),
        # BLOCKED 允许恢复，便于未来接入澄清、重试或人工解锁流程。
        AgentRunState.BLOCKED: frozenset(
            {
                AgentRunState.RUNNING,
                AgentRunState.FAILED,
                AgentRunState.CANCELLED,
            }
        ),
        AgentRunState.SUCCEEDED: frozenset(),
        AgentRunState.FAILED: frozenset(),
        AgentRunState.CANCELLED: frozenset(),
    }

    def can_transition(
        self,
        current: AgentRunState,
        target: AgentRunState,
    ) -> bool:
        """判断状态转换是否合法；同状态转换视为幂等成功。"""

        return current == target or target in self._ALLOWED_TRANSITIONS[current]

    def transition(
        self,
        context: RuntimeContext,
        target: AgentRunState,
        *,
        reason: str,
    ) -> AgentRunState:
        """校验并应用状态转换。

        非法转换统一抛出 RuntimeFatalError，避免调用方静默覆盖运行状态。
        """

        current = context.state
        # 幂等转换直接返回当前状态，避免重复事件导致无意义报错。
        if current == target:
            return current

        if not self.can_transition(current, target):
            raise RuntimeFatalError(
                "RUNTIME_INVALID_STATE_TRANSITION",
                f"Runtime 状态不允许从 {current.value} 转换为 {target.value}",
                details={
                    "current_state": current.value,
                    "target_state": target.value,
                    "reason": reason,
                },
                run_id=context.run_id,
            )

        # 只有状态机可以写入 RuntimeContext.state。
        context.state = target
        return target


__all__ = ["RuntimeStateMachine"]

