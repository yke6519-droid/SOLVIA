"""R3-E：把已获准的用户意图转换成安全的工具参数更新。"""

from __future__ import annotations

from backend.app.runtime.enums import AgentRunState, InteractionIntent, InteractionState
from backend.app.runtime.exceptions import RuntimeInteractionError
from backend.app.runtime.models import (
    ArgumentPatch,
    ConfirmationRequest,
    InteractionResolution,
    RuntimeContext,
    ToolInvocation,
    ToolSpec,
)


class IntentApplier:
    """只处理参数白名单和字段边界，不执行真实业务工具。"""

    _PATCH_INTENTS = frozenset(
        {
            InteractionIntent.MODIFY,
            InteractionIntent.SELECT,
            InteractionIntent.PROVIDE,
        }
    )

    def build_patch(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
        request: ConfirmationRequest,
        resolution: InteractionResolution,
    ) -> ArgumentPatch:
        """将意图 slots 转成一次受白名单约束的参数更新。"""

        if resolution.state != InteractionState.CONFIRMED:
            return ArgumentPatch()
        if resolution.intent == InteractionIntent.CONFIRM:
            return ArgumentPatch()
        if resolution.intent not in self._PATCH_INTENTS:
            return ArgumentPatch()

        updates = resolution.slots
        if not updates:
            self._raise_invalid(
                context,
                invocation,
                spec,
                "用户意图没有提供可应用的参数。",
                fields=[],
            )

        editable_fields = set(request.editable_fields)
        schema_fields = set(
            (spec.args_schema.get("properties") or {})
            if isinstance(spec.args_schema, dict)
            else {}
        )
        invalid_fields = sorted(set(updates) - editable_fields)
        if schema_fields:
            invalid_fields.extend(sorted(set(updates) - schema_fields))
            invalid_fields = sorted(set(invalid_fields))
        if invalid_fields:
            self._raise_invalid(
                context,
                invocation,
                spec,
                "用户意图尝试修改未开放的工具参数。",
                fields=invalid_fields,
            )

        return ArgumentPatch(updates=dict(updates))

    def apply(
        self,
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
        request: ConfirmationRequest,
        resolution: InteractionResolution,
    ) -> ArgumentPatch:
        """生成补丁并写回当前 ToolInvocation，返回补丁供调用方记录。"""

        patch = self.build_patch(context, invocation, spec, request, resolution)
        if patch.updates:
            # 只合并已通过白名单检查的字段；最终类型校验仍由原始 Tool Schema 完成。
            invocation.arguments = {**invocation.arguments, **patch.updates}
        return patch

    @staticmethod
    def _raise_invalid(
        context: RuntimeContext,
        invocation: ToolInvocation,
        spec: ToolSpec,
        message: str,
        *,
        fields: list[str],
    ) -> None:
        raise RuntimeInteractionError(
            "RUNTIME_INTENT_ARGUMENT_INVALID",
            message,
            run_state=AgentRunState.BLOCKED,
            details={
                "tool_name": spec.name,
                "call_id": invocation.call_id,
                "fields": fields,
            },
            run_id=context.run_id,
            call_id=invocation.call_id,
        )


__all__ = ["IntentApplier"]
