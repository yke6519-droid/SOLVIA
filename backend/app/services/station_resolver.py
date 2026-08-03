"""任务级站点解析。

所有工具通过本模块解析用户提供的站点引用。遇到多个候选时只返回结构化
歧义信息，由 Agent 调用 ask_user 获取自然语言回复，再用精确站点名称或
ID 重新调用工具；本模块不直接等待用户，也不解析固定序号。
"""

from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass, field
from typing import Any

from backend.app.errors import ToolError


@dataclass
class StationResolutionContext:
    """Resolved station aliases that are valid for one Agent execution only."""

    resolved_by_alias: dict[str, dict[str, Any]] = field(default_factory=dict)
    # A previously confirmed station loaded from the session context.  This is
    # intentionally a single small object, not a copy of the conversation.
    active_station: dict[str, Any] | None = None
    # The station explicitly selected through ask_user during this execution.
    # chat.py persists it only after the Agent task completes successfully.
    last_user_selected_station: dict[str, Any] | None = None
    # Candidate IDs waiting for Agent/ask_user to resolve the ambiguity.
    pending_selection_ids: set[str] = field(default_factory=set)


_current_context: contextvars.ContextVar[StationResolutionContext | None] = contextvars.ContextVar(
    "station_resolution_context",
    default=None,
)


def bind_station_resolution_context(
    *,
    active_station: dict[str, Any] | None = None,
):
    """Create an isolated station-resolution cache for one Agent task.

    ``active_station`` is the small, persisted session hint.  It is only used
    for contextual references such as ``它`` or ``刚才那个站点``; an explicit
    region or station name always takes precedence.
    """
    return _current_context.set(
        StationResolutionContext(
            active_station=_copy_station(active_station) if active_station else None,
        )
    )


def reset_station_resolution_context(token) -> None:
    """Discard task-local choices after an Agent task completes."""
    _current_context.reset(token)


def get_station_resolution_context() -> StationResolutionContext | None:
    """Return the current task context for controlled post-task persistence."""
    return _current_context.get()


def _reference_key(value: str) -> str:
    """Normalize a user reference without changing its business meaning."""
    return re.sub(r"\s+", "", str(value or "")).lower()


def _copy_station(station: dict[str, Any]) -> dict[str, Any]:
    return dict(station)


def _is_contextual_reference(reference: str) -> bool:
    """Recognize references that intentionally point to the prior active site."""
    normalized = re.sub(r"\s+", "", reference)
    return normalized in {
        "它",
        "该站点",
        "这个站点",
        "刚才那个站点",
        "刚才的站点",
        "刚才那个",
        "上一个站点",
    }


class StationResolver:
    """Resolve a station once and reuse the structured result within one task."""

    def resolve(
        self,
        station_name: str,
        *,
        stations: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        reference = str(station_name or "").strip()
        if not reference:
            return None

        context = _current_context.get()
        matches = self.find_candidates(reference, stations=stations)

        if not matches:
            return None

        if len(matches) > 1:
            # 业务解析层只报告歧义，不等待用户，也不解析“第几个”。
            # Agent 会通过 ask_user 获取自然语言回复，再选择精确站点重试。
            self.mark_selection_pending(matches)
            raise ToolError(
                "STATION_SELECTION_REQUIRED",
                f"“{reference}”匹配到 {len(matches)} 个站点，需要用户选择。",
                details=station_selection_payload(reference, matches),
                retryable=True,
            )

        chosen = matches[0]

        if context and str(chosen.get("station_id")) in context.pending_selection_ids:
            context.last_user_selected_station = _copy_station(chosen)
            context.pending_selection_ids.clear()

        self._cache_resolution(context, reference, chosen)
        return _copy_station(chosen)

    def find_candidates(
        self,
        station_name: str,
        *,
        stations: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """只查找候选站点，不触发用户交互。"""

        reference = str(station_name or "").strip()
        if not reference:
            return []

        context = _current_context.get()
        cache_key = _reference_key(reference)

        # 上一轮已经选定的站点和当前任务内的别名缓存优先复用。
        if context and context.active_station and _is_contextual_reference(reference):
            return [_copy_station(context.active_station)]
        if context and cache_key in context.resolved_by_alias:
            return [_copy_station(context.resolved_by_alias[cache_key])]

        if stations is None:
            from backend.app.services.station_catalog_service import load_stations_from_db

            stations = load_stations_from_db()

        from backend.app.services.station_catalog_service import match_all_stations

        return [_copy_station(item) for item in match_all_stations(reference, stations)]

    def mark_selection_pending(self, matches: list[dict[str, Any]]) -> None:
        """记录一次待用户选择的候选集合，供后续成功解析时保存上下文。"""

        context = _current_context.get()
        if context is not None:
            context.pending_selection_ids = {
                str(item.get("station_id")) for item in matches
            }

    @staticmethod
    def _cache_resolution(
        context: StationResolutionContext | None,
        raw_reference: str,
        station: dict[str, Any],
    ) -> None:
        if context is None:
            return
        snapshot = _copy_station(station)
        aliases = {
            raw_reference,
            str(station.get("station_id", "")),
            str(station.get("name", "")),
        }
        for alias in aliases:
            key = _reference_key(alias)
            if key:
                context.resolved_by_alias[key] = snapshot

station_resolver = StationResolver()


def station_selection_payload(
    reference: str,
    matches: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成 Agent 可交给 ask_user 和后续 LLM 判断的候选站点数据。"""

    return {
        "reference": reference,
        "candidate_count": len(matches),
        "candidates": [
            {
                "index": index,
                "station_id": item.get("station_id"),
                "name": item.get("name"),
                "capacity_kw": item.get("capacity_kw"),
                "location": item.get("location", ""),
            }
            for index, item in enumerate(matches, 1)
        ],
    }


def resolve_station(
    station_name: str,
    *,
    stations: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Convenience entry point for legacy tools and new business services."""
    return station_resolver.resolve(station_name, stations=stations)
