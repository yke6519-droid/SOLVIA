"""R3 泛化人机交互的意图解释层。

解释器只负责把自然语言回复转换成稳定的 UserIntent；
InteractionPolicy 再根据当前交互允许的意图和置信度做最终裁决。
这样可以替换底层 LLM，而不会把 LLM 判断直接接到工具执行权限上。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from backend.app.runtime.enums import (
    InteractionIntent,
    InteractionState,
    IntentSource,
    RuntimeInteractionCode,
)
from backend.app.runtime.models import PendingInteraction, UserIntent


# 保留原日志名称，避免迁移后现有日志查询和测试失效。
logger = logging.getLogger("backend.app.runtime.intent_interpreter")


def normalize_user_reply(raw_reply: Any) -> str:
    """去除传输层前缀并统一空白，避免解释器依赖 AskUserBridge 的格式。"""

    response = str(raw_reply or "").strip()
    for prefix in ("用户回复:", "用户回复："):
        if response.startswith(prefix):
            response = response[len(prefix) :].strip()
            break
    return response


class UserIntentInterpreter(Protocol):
    """用户回复解释器的最小接口，可由规则、LLM 或组合实现。"""

    def interpret(
        self,
        interaction: PendingInteraction,
        raw_reply: str,
    ) -> UserIntent:
        """将用户自然语言回复转换为结构化意图。"""


class DeterministicIntentInterpreter:
    """确定性快速解释器。"""

    _CANCEL_PATTERNS = (
        "取消",
        "不用了",
        "别做",
        "先别",
        "停止",
        "不执行",
        "不想执行",
        "不确认",
        "不要确认",
        "不想确认",
        "不要执行",
        "不需要了",
    )
    _CONFIRM_PATTERNS = (
        "确认",
        "确定",
        "继续",
        "开始",
        "按这个",
        "就这样",
        "可以",
        "没问题",
        "执行吧",
        "就按这个执行",
    )
    _MODIFY_PATTERNS = ("改成", "修改", "换成", "调整")
    _SELECT_PATTERNS = ("第一个", "第二个", "第三个", "选")
    _PROVIDE_PATTERNS = ("导出成", "格式用", "使用", "补充")

    def interpret(
        self,
        interaction: PendingInteraction,
        raw_reply: str,
    ) -> UserIntent:
        del interaction
        response = normalize_user_reply(raw_reply)
        compact = "".join(response.lower().split())

        # 取消优先于确认，避免“我不想确认”被“确认”误判为放行。
        if any(pattern in compact for pattern in self._CANCEL_PATTERNS):
            return UserIntent(
                intent=InteractionIntent.CANCEL,
                confidence=0.99,
                source=IntentSource.DETERMINISTIC,
                reason="命中明确的取消表达",
            )
        if any(pattern in compact for pattern in self._CONFIRM_PATTERNS):
            return UserIntent(
                intent=InteractionIntent.CONFIRM,
                confidence=0.99,
                source=IntentSource.DETERMINISTIC,
                reason="命中明确的确认表达",
            )
        if any(pattern in compact for pattern in self._MODIFY_PATTERNS):
            return UserIntent(
                intent=InteractionIntent.MODIFY,
                confidence=0.90,
                source=IntentSource.DETERMINISTIC,
                reason="命中修改表达",
            )
        if any(pattern in compact for pattern in self._SELECT_PATTERNS):
            return UserIntent(
                intent=InteractionIntent.SELECT,
                confidence=0.90,
                source=IntentSource.DETERMINISTIC,
                reason="命中选择表达",
            )
        if any(pattern in compact for pattern in self._PROVIDE_PATTERNS):
            return UserIntent(
                intent=InteractionIntent.PROVIDE,
                confidence=0.90,
                source=IntentSource.DETERMINISTIC,
                reason="命中补充信息表达",
            )
        return UserIntent(
            intent=InteractionIntent.UNCLEAR,
            source=IntentSource.FALLBACK,
            reason="确定性规则无法判断用户意图",
        )


class LLMIntentInterpreter:
    """使用已注入的 LLM 解释模糊回复，不在这里创建新的网络客户端。"""

    def __init__(self, llm: Any, *, method: str = "json_mode") -> None:
        if method not in {"function_calling", "json_mode", "json_schema"}:
            raise ValueError(f"不支持的 LLM 结构化输出方式: {method}")
        self._llm = llm
        self._method = method

    def interpret(
        self,
        interaction: PendingInteraction,
        raw_reply: str,
    ) -> UserIntent:
        response = normalize_user_reply(raw_reply)
        prompt = (
            "你是 Runtime 人机交互意图分类器。\n"
            "只根据给定问题和用户回复，返回符合 UserIntent schema 的结构化结果。\n"
            "只输出一个 JSON 对象，不要输出 Markdown 代码块、解释文字或工具调用。\n"
            "不要执行工具，不要替 Runtime 做最终放行决定。\n"
            f"交互问题：{interaction.question}\n"
            f"允许的意图：{', '.join(intent.value for intent in interaction.allowed_intents)}\n"
            f"允许修改的参数字段：{', '.join(interaction.editable_fields) or '无'}\n"
            f"用户回复：{response}\n"
            "如果意图是 modify、select 或 provide，slots 只能使用允许修改的字段，"
            "并填写工具可以直接接收的最终参数值。\n"
            "如果无法确定，intent 返回 unclear，confidence 返回 0。"
        )
        try:
            structured_llm = self._llm.with_structured_output(
                UserIntent,
                method=self._method,
            )
            value = structured_llm.invoke(prompt)
            if isinstance(value, UserIntent):
                parsed = value
            elif isinstance(value, Mapping):
                parsed = UserIntent.model_validate(value)
            else:
                raise TypeError("LLM 结构化输出不是 UserIntent 或对象映射")
            parsed = parsed.model_copy(
                update={
                    "source": IntentSource.LLM,
                    "reason": parsed.reason or "由 LLM 根据问题和回复完成分类",
                }
            )
            # 不记录用户原文和 slots，只记录可用于判断 LLM 是否生效的摘要。
            logger.info(
                "Runtime LLM 意图解释完成: interaction_id=%s intent=%s confidence=%.2f",
                interaction.interaction_id,
                parsed.intent.value,
                parsed.confidence,
            )
            return parsed
        except Exception as exc:
            # 解释失败不能放行工具；交给策略层按 UNCLEAR 处理。
            logger.warning(
                "Runtime LLM 意图解释失败: interaction_id=%s error_type=%s",
                interaction.interaction_id,
                type(exc).__name__,
            )
            return UserIntent(
                intent=InteractionIntent.UNCLEAR,
                source=IntentSource.FALLBACK,
                reason=f"LLM 意图解释失败: {type(exc).__name__}",
            )


class HybridUserIntentInterpreter:
    """确定性快速路径 + 模糊回复 LLM 兜底。"""

    def __init__(
        self,
        llm_interpreter: UserIntentInterpreter | None = None,
        *,
        deterministic_interpreter: UserIntentInterpreter | None = None,
    ) -> None:
        self._deterministic = deterministic_interpreter or DeterministicIntentInterpreter()
        self._llm = llm_interpreter

    def interpret(
        self,
        interaction: PendingInteraction,
        raw_reply: str,
    ) -> UserIntent:
        fast_result = self._deterministic.interpret(interaction, raw_reply)
        # 确认/取消可以直接走关键词；参数修改类意图还需要 LLM 提取 slots。
        needs_slot_extraction = fast_result.intent in {
            InteractionIntent.MODIFY,
            InteractionIntent.SELECT,
            InteractionIntent.PROVIDE,
        } and not fast_result.slots
        if (
            (fast_result.intent != InteractionIntent.UNCLEAR and not needs_slot_extraction)
            or self._llm is None
        ):
            return fast_result
        return self._llm.interpret(interaction, raw_reply)


class InteractionPolicy:
    """把意图解释结果转换为 Runtime 状态，掌握最终安全边界。"""

    def __init__(self, *, confirm_threshold: float = 0.85) -> None:
        if not 0 <= confirm_threshold <= 1:
            raise ValueError("confirm_threshold 必须位于 0 到 1 之间")
        self.confirm_threshold = confirm_threshold

    def evaluate(
        self,
        interaction: PendingInteraction,
        user_intent: UserIntent,
    ) -> tuple[InteractionState, RuntimeInteractionCode | None]:
        allowed = set(interaction.allowed_intents)

        # 取消是安全收敛动作：只要分类为取消，就不执行工具。
        if (
            user_intent.intent == InteractionIntent.CANCEL
            and InteractionIntent.CANCEL in allowed
        ):
            return InteractionState.CANCELLED, RuntimeInteractionCode.INTERACTION_CANCELLED

        if (
            user_intent.intent in allowed
            and user_intent.confidence >= self.confirm_threshold
        ):
            return InteractionState.CONFIRMED, None

        return InteractionState.REJECTED, RuntimeInteractionCode.INVALID_RESPONSE


__all__ = [
    "DeterministicIntentInterpreter",
    "HybridUserIntentInterpreter",
    "InteractionPolicy",
    "LLMIntentInterpreter",
    "UserIntentInterpreter",
    "normalize_user_reply",
]
