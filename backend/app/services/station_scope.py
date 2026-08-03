"""Resolve the station scope requested by one Agent turn.

This module intentionally stays smaller than a task planner.  It only answers
which stations the current request refers to: one station, an explicit list,
or all stations in a region.  Prediction, weather, charting, and export code
continue to consume the returned structured station dictionaries.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import ToolException

from backend.app.services.station_catalog_service import query_stations_by_region


REGION_QUERY_MAX_STATIONS = 20

def normalize_region(region: str) -> str:
    """Extract a database-search keyword from a user region phrase.

    The database is the source of truth for available regions, so this
    function deliberately does not maintain a city/province allow-list.  For
    example, both ``衢州地区`` and ``衢州市`` become the keyword ``衢州`` and
    are matched against the stored province, city, location, and station name.
    """
    value = re.sub(r"\s+", "", str(region or "")).strip(" ，。！？")
    if not value:
        raise ToolException("地区不能为空")

    # The Agent normally passes only the region argument.  These suffixes
    # make the tool tolerant when it forwards a short natural-language phrase.
    value = re.sub(r"^(看一下|看下|查询一下|查询|查看一下|查看|查一下|查)", "", value)
    value = re.sub(r"(地区|区域|所有站点|全部站点|站点|的)+$", "", value)
    value = re.sub(r"(市|省)$", "", value)
    if not value:
        raise ToolException("地区关键词不能为空")
    return value


def select_region_stations(
    stations: list[dict[str, Any]],
    *,
    region: str,
    max_stations: int = REGION_QUERY_MAX_STATIONS,
) -> dict[str, Any]:
    """Validate and format a region result; useful for tests and adapters."""
    if max_stations < 1:
        raise ValueError("max_stations must be positive")
    if len(stations) > max_stations:
        raise ToolException(
            f"{region}共有 {len(stations)} 个有效站点，超过单次最多处理 "
            f"{max_stations} 个站点的限制，请缩小地区范围或分批执行"
        )
    return {
        "scope_type": "region",
        "region": region,
        "station_count": len(stations),
        "stations": stations,
    }


class StationScopeResolver:
    """Resolve one, many, or region-scoped station references.

    The resolver does not execute predictions and does not create a task plan;
    it only returns a normalized list of station dictionaries for downstream
    business functions to consume.
    """

    def resolve_single(
        self,
        station_name: str,
        *,
        stations: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from backend.app.services.station_resolver import resolve_station

        station = resolve_station(station_name, stations=stations)
        if station is None:
            raise ToolException(f"未找到站点：{station_name}")
        return station

    def resolve_many(
        self,
        station_names: list[str],
        *,
        stations: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """逐个解析明确名称；歧义项交给 Agent 通过 ask_user 处理。"""
        resolved: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for name in station_names:
            station = self.resolve_single(name, stations=stations)
            station_id = str(station.get("station_id"))
            if station_id not in seen_ids:
                resolved.append(station)
                seen_ids.add(station_id)
        return resolved

    def resolve_region(
        self,
        region: str,
        *,
        max_stations: int = REGION_QUERY_MAX_STATIONS,
    ) -> dict[str, Any]:
        normalized_region = normalize_region(region)
        stations = query_stations_by_region(
            region_keyword=normalized_region,
            limit=max_stations + 1,
        )
        if not stations:
            raise ToolException(f"未找到{normalized_region}的有效光伏站点")
        return select_region_stations(
            stations,
            region=normalized_region,
            max_stations=max_stations,
        )


station_scope_resolver = StationScopeResolver()
