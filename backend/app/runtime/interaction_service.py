"""R3 Runtime 交互协调服务。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from backend.app.runtime.enums import (
    AgentEventType,
    AgentRunState,
    InteractionIntent,
    InteractionState,
    IntentSource,
    RuntimeInteractionCode,
)
from backend.app.runtime.models import AgentEvent
from backend.app.runtime.models import (
    InteractionResolution,
    PendingInteraction,
)
from backend.app.runtime.models import RuntimeContext
from backend.app.runtime.models import ToolInvocation
from backend.app.runtime.intent_interpreter import (
    HybridUserIntentInterpreter,
    InteractionPolicy,
    UserIntentInterpreter,
    normalize_user_reply,
)
from backend.app.runtime.state_machine import RuntimeStateMachine


class InteractionTransport(Protocol):
    """Runtime 所需的最小交互传输接口。"""

    def ask(self, question: str) -> str:
        """向用户发送问题并等待文本回复。"""


def _utc_now() -> datetime:
    """生成统一的 UTC 时间，便于测试注入时钟。"""

    return datetime.now(timezone.utc)


class RuntimeInteractionService:
    """连接 Runtime 状态与现有 AskUserBridge 的薄协调层。"""

    def __init__(
        self,
        transport: InteractionTransport | Callable[[str], str],
        *,
        event_sink: Callable[[AgentEvent], None] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        intent_interpreter: UserIntentInterpreter | None = None,
        interaction_policy: InteractionPolicy | None = None,
        state_machine: RuntimeStateMachine | None = None,
    ) -> None:
        self._transport = transport
        self._event_sink = event_sink
        self._clock = clock
        # 默认只启用确定性解释器；生产装配时可注入带 LLM 兜底的解释器。
        self._intent_interpreter = intent_interpreter or HybridUserIntentInterpreter()
        self._interaction_policy = interaction_policy or InteractionPolicy()
        self._state_machine = state_machine or RuntimeStateMachine()

    async def arequest_confirmation(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        *,
        confirmation_key: str,
        question: str,
        timeout_seconds: float | None = None,
        allowed_intents: list[InteractionIntent] | None = None,
    ) -> InteractionResolution:
        """异步入口：将现有同步 Bridge 放到线程中，避免阻塞 SSE 事件循环。"""

        return await asyncio.to_thread(
            self.request_confirmation,
            context,
            invocation,
            confirmation_key=confirmation_key,
            question=question,
            timeout_seconds=timeout_seconds,
            allowed_intents=allowed_intents,
        )

    def request_confirmation(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        *,
        confirmation_key: str,
        question: str,
        timeout_seconds: float | None = None,
        allowed_intents: list[InteractionIntent] | None = None,
    ) -> InteractionResolution:
        """发起一次确认，并将用户回复转换为稳定交互结果。"""

        previous = self._find_previous(context, confirmation_key)
        if previous is not None:
            return self._resolution_from_interaction(previous)

        if context.pending_interaction is not None:
            raise RuntimeError(
                "当前 Runtime 已有待处理交互，不能并行创建第二个交互"
            )

        created_at = self._clock()
        expires_at = (
            created_at + timedelta(seconds=timeout_seconds)
            if timeout_seconds is not None
            else None
        )
        interaction = PendingInteraction(
            run_id=context.run_id,
            call_id=invocation.call_id,
            confirmation_key=confirmation_key,
            question=question,
            created_at=created_at,
            expires_at=expires_at,
            allowed_intents=list(
                allowed_intents
                or [InteractionIntent.CONFIRM, InteractionIntent.CANCEL]
            ),
        )
        context.pending_interaction = interaction
        self._state_machine.transition(
            context,
            AgentRunState.WAITING_USER,
            reason="interaction_requested",
        )
        self._emit(
            context,
            AgentEventType.INTERACTION_REQUESTED,
            interaction,
            {"question": question},
        )
        self._emit(context, AgentEventType.RUN_WAITING_USER, interaction)

        if callable(self._transport):
            raw_response = self._transport(question)
        else:
            raw_response = self._transport.ask(question)
        resolution = self._resolve(interaction, raw_response)
        resolved = interaction.model_copy(
            update={
                "status": resolution.state,
                "resolved_at": self._clock(),
                "response": resolution.response,
                "resolved_intent": resolution.intent,
                "intent_confidence": resolution.confidence,
                "intent_source": resolution.source,
                "intent_reason": resolution.reason,
                "intent_slots": resolution.slots,
            }
        )
        context.interaction_history.append(resolved)
        context.pending_interaction = None
        self._apply_run_state(context, resolved.status)
        self._emit_resolution_event(context, resolved, resolution)
        return resolution

    @staticmethod
    def _find_previous(
        context: RuntimeContext,
        confirmation_key: str,
    ) -> PendingInteraction | None:
        """查找同一运行中已经处理过的确认，避免重复询问。"""

        for interaction in reversed(context.interaction_history):
            if interaction.confirmation_key == confirmation_key:
                return interaction
        return None

    @staticmethod
    def _resolution_from_interaction(
        interaction: PendingInteraction,
    ) -> InteractionResolution:
        """将历史交互转换为幂等返回值。"""

        code = {
            InteractionState.CANCELLED: RuntimeInteractionCode.INTERACTION_CANCELLED,
            InteractionState.EXPIRED: RuntimeInteractionCode.INTERACTION_TIMEOUT,
            InteractionState.REJECTED: RuntimeInteractionCode.INVALID_RESPONSE,
        }.get(interaction.status)
        return InteractionResolution(
            interaction_id=interaction.interaction_id,
            state=interaction.status,
            code=code,
            response=interaction.response,
            intent=interaction.resolved_intent or InteractionIntent.UNCLEAR,
            confidence=interaction.intent_confidence or 0,
            source=interaction.intent_source or IntentSource.FALLBACK,
            reason=interaction.intent_reason,
            slots=interaction.intent_slots,
        )

    def _resolve(
        self,
        interaction: PendingInteraction,
        raw_response: str,
    ) -> InteractionResolution:
        """先处理超时，再解释意图，最后由策略层决定 Runtime 状态。"""

        response = normalize_user_reply(raw_response)
        compact = "".join(response.lower().split())
        if "超时" in compact:
            return InteractionResolution(
                interaction_id=interaction.interaction_id,
                state=InteractionState.EXPIRED,
                code=RuntimeInteractionCode.INTERACTION_TIMEOUT,
                response=response,
                reason="交互回复被标记为超时",
            )

        user_intent = self._intent_interpreter.interpret(interaction, response)
        state, code = self._interaction_policy.evaluate(interaction, user_intent)
        return InteractionResolution(
            interaction_id=interaction.interaction_id,
            state=state,
            code=code,
            response=response,
            intent=user_intent.intent,
            confidence=user_intent.confidence,
            source=user_intent.source,
            reason=user_intent.reason,
            slots=user_intent.slots,
        )

    def _apply_run_state(
        self,
        context: RuntimeContext,
        interaction_state: InteractionState,
    ) -> None:
        """将交互结果映射为当前运行状态。"""

        target_state = {
            InteractionState.CONFIRMED: AgentRunState.RUNNING,
            InteractionState.CANCELLED: AgentRunState.CANCELLED,
            InteractionState.EXPIRED: AgentRunState.BLOCKED,
            InteractionState.REJECTED: AgentRunState.BLOCKED,
        }[interaction_state]
        self._state_machine.transition(
            context,
            target_state,
            reason=f"interaction_{interaction_state.value}",
        )

    def _emit(
        self,
        context: RuntimeContext,
        event_type: AgentEventType,
        interaction: PendingInteraction,
        extra: dict | None = None,
    ) -> None:
        """发布可选 Runtime 事件；没有 sink 时不影响核心交互流程。"""

        if self._event_sink is None:
            return
        payload = {
            "interaction_id": interaction.interaction_id,
            "confirmation_key": interaction.confirmation_key,
        }
        if extra:
            payload.update(extra)
        self._event_sink(
            AgentEvent(
                run_id=context.run_id,
                call_id=interaction.call_id,
                event_type=event_type,
                payload=payload,
            )
        )

    def _emit_resolution_event(
        self,
        context: RuntimeContext,
        interaction: PendingInteraction,
        resolution: InteractionResolution,
    ) -> None:
        """发布确认后的恢复、取消或超时事件。"""

        event_type = {
            InteractionState.CONFIRMED: AgentEventType.RUN_RESUMED,
            InteractionState.CANCELLED: AgentEventType.RUN_CANCELLED,
            InteractionState.EXPIRED: AgentEventType.INTERACTION_EXPIRED,
            InteractionState.REJECTED: AgentEventType.RUN_FAILED,
        }[resolution.state]
        self._emit(
            context,
            event_type,
            interaction,
            {
                "state": resolution.state.value,
                "code": resolution.code.value if resolution.code else None,
                "intent": resolution.intent.value,
                "intent_confidence": resolution.confidence,
                "intent_source": resolution.source.value,
            },
        )

__all__ = ["InteractionTransport", "RuntimeInteractionService"]
