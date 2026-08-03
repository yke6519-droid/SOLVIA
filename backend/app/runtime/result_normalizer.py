"""将不同形态的业务工具输出统一转换为 Runtime ToolResult。"""

from __future__ import annotations

import json
from typing import Any

from backend.app.runtime.enums import ToolResultStatus
from backend.app.runtime.models import ToolResult


_MAX_TEXT_LENGTH = 160
_SUMMARY_KEYS = (
    "status",
    "code",
    "result_type",
    "artifact_id",
    "artifact_ids",
    "file_id",
    "chart_id",
    "row_count",
)


def _to_mapping(value: Any) -> dict[str, Any] | None:
    """尽量把工具输出读取为字典，不对普通业务文本做猜测。"""

    if isinstance(value, ToolResult):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    if hasattr(value, "content"):
        value = value.content
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _safe_text(value: Any) -> str | None:
    """限制进入 Runtime 协议的文本长度，避免日志携带完整业务结果。"""

    if value in (None, ""):
        return None
    text = str(value)
    return text if len(text) <= _MAX_TEXT_LENGTH else f"{text[:_MAX_TEXT_LENGTH]}..."


def _extract_artifact_ids(value: Any) -> list[str]:
    """提取制品 ID；限制数量，避免异常工具输出无限扩张事件。"""

    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")][:20]
    return []


def _summarize_value(value: Any) -> Any:
    """只为审计保留小型标量；列表只记录长度。"""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, (list, tuple, set)):
        return {"type": "list", "length": len(value)}
    return {"type": type(value).__name__}


class ResultNormalizer:
    """RuntimeEngine 与 RuntimeObserver 共用的工具结果标准化器。

    标准化只生成 Runtime 使用的 ``ToolResult``，不会改变原始业务输出。
    因此 Agent 仍能收到原 Tool 的字符串或字典，Hook 和 Observer 则读取统一
    的状态、错误码、重试属性和 Artifact 引用。
    """

    def summarize(self, raw_output: Any) -> dict[str, Any]:
        """生成安全的工具结果摘要，不保存完整业务数据。"""

        mapping = _to_mapping(raw_output)
        if mapping is None:
            return {
                "type": "text",
                "length": len(str(raw_output or "")),
            }

        summary: dict[str, Any] = {"type": "object"}
        for key in _SUMMARY_KEYS:
            if key in mapping and mapping[key] not in (None, ""):
                summary[key] = _summarize_value(mapping[key])

        return summary

    def normalize(self, raw_output: Any) -> ToolResult:
        """把原始工具输出转换为 Runtime 内部统一的 ToolResult。"""

        if isinstance(raw_output, ToolResult):
            return raw_output

        mapping = _to_mapping(raw_output)
        status = ToolResultStatus.SUCCESS
        code = None
        message = None
        retryable = False
        suggested_actions: list[str] = []
        artifact_ids: list[str] = []

        if mapping:
            raw_status = str(mapping.get("status", "")).lower()
            legacy_protocol = raw_status in {"rejected", "error"} or "error" in mapping
            if raw_status in {item.value for item in ToolResultStatus}:
                status = ToolResultStatus(raw_status)
            elif legacy_protocol:
                # 不再读取旧 error 内容；只拒绝旧协议，避免它被默认当成成功。
                status = ToolResultStatus.RECOVERABLE_ERROR

            code = _safe_text(mapping.get("code"))
            message = _safe_text(mapping.get("message"))
            retryable = bool(mapping.get("retryable", False))
            if legacy_protocol:
                code = "RUNTIME_RESULT_PROTOCOL_INVALID"
                message = "工具返回结果不符合统一 Runtime 协议。"
                retryable = False

            raw_actions = mapping.get("suggested_actions", [])
            if isinstance(raw_actions, list):
                suggested_actions = [
                    str(item)
                    for item in raw_actions
                    if item not in (None, "")
                ][:10]

            artifact_ids = _extract_artifact_ids(mapping.get("artifact_ids"))
            if mapping.get("artifact_id"):
                artifact_ids.extend(
                    _extract_artifact_ids(mapping.get("artifact_id"))
                )

        return ToolResult(
            status=status,
            code=code,
            message=message,
            retryable=retryable,
            suggested_actions=suggested_actions,
            artifact_ids=list(dict.fromkeys(artifact_ids)),
            data=self.summarize(raw_output),
        )


__all__ = ["ResultNormalizer"]
