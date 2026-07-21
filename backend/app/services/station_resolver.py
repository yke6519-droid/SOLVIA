"""Task-scoped station resolution with one-time ambiguity handling.

All tools resolve user-facing station references through this module. A choice
made for an ambiguous name is kept only for the active Agent turn, preventing
duplicate prompts without silently carrying a choice into a later task.
"""

from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import ToolException


@dataclass
class StationResolutionContext:
    """Resolved station aliases that are valid for one Agent execution only."""

    resolved_by_alias: dict[str, dict[str, Any]] = field(default_factory=dict)


_current_context: contextvars.ContextVar[StationResolutionContext | None] = contextvars.ContextVar(
    "station_resolution_context",
    default=None,
)


def bind_station_resolution_context():
    """Create an isolated station-resolution cache for one Agent task."""
    return _current_context.set(StationResolutionContext())


def reset_station_resolution_context(token) -> None:
    """Discard task-local choices after an Agent task completes."""
    _current_context.reset(token)


def _reference_key(value: str) -> str:
    """Normalize a user reference without changing its business meaning."""
    return re.sub(r"\s+", "", str(value or "")).lower()


def _copy_station(station: dict[str, Any]) -> dict[str, Any]:
    return dict(station)


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
        cache_key = _reference_key(reference)
        if context and cache_key in context.resolved_by_alias:
            return _copy_station(context.resolved_by_alias[cache_key])

        if stations is None:
            from backend.tools.weather_fetcher_tool import _load_stations_from_db

            stations = _load_stations_from_db()

        from backend.tools.weather_fetcher_tool import _match_all_stations

        matches = _match_all_stations(reference, stations)
        if not matches:
            return None

        if len(matches) == 1:
            chosen = matches[0]
        else:
            chosen = self._choose_ambiguous_station(reference, matches)

        self._cache_resolution(context, reference, chosen)
        return _copy_station(chosen)

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

    @staticmethod
    def _choose_ambiguous_station(
        reference: str,
        matches: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Ask once for an explicit index and return the selected station."""
        from backend.tools.ask_user_tool import request_user_input

        options = [
            f"  {index}. {info['name']} (ID:{info['station_id']}, "
            f"位置:{info.get('location', '未知')})"
            for index, info in enumerate(matches, 1)
        ]
        question = (
            f"“{reference}”匹配到 {len(matches)} 个站点，请选择本次任务使用的站点：\n"
            + "\n".join(options)
            + f"\n请输入序号（1-{len(matches)}）"
        )
        print(f"⚠️ '{reference}' 匹配到 {len(matches)} 个站点，需要用户选择:")
        for option in options:
            print(option)

        answer = str(request_user_input(question) or "").strip()
        for prefix in ("用户回复:", "用户回复："):
            if answer.startswith(prefix):
                answer = answer[len(prefix):].strip()
                break

        try:
            index = int(answer) - 1
        except ValueError as exc:
            raise ToolException("站点选择无效，请回复候选站点的序号后重新执行任务。") from exc

        if not 0 <= index < len(matches):
            raise ToolException(
                f"站点选择超出范围，请回复 1 到 {len(matches)} 之间的序号后重新执行任务。"
            )
        chosen = matches[index]
        print(f"✅ 用户选择了: {chosen['name']}")
        return chosen


station_resolver = StationResolver()


def resolve_station(
    station_name: str,
    *,
    stations: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Convenience entry point for legacy tools and new business services."""
    return station_resolver.resolve(station_name, stations=stations)
