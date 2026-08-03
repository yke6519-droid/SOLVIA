"""站点信息查询工具。

本文件只放 Agent 可直接调用的站点目录工具：单站点信息、位置查询和
地区站点查询。具体 SQL 和名称匹配由站点目录 Service 完成，避免和天气、
发电量、预测工具混在一起。
"""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import ToolException, tool

from backend.app.services.station_catalog_service import load_stations_from_db
from backend.app.services.station_resolver import (
    resolve_station,
    station_resolver,
    station_selection_payload,
)
from backend.app.services.station_scope import (
    REGION_QUERY_MAX_STATIONS,
    station_scope_resolver,
)


def _format_station_list(stations: dict[str, dict]) -> str:
    """把站点目录统一格式化为 Agent 可读的列表。"""
    lines = [f"共 {len(stations)} 个可用站点"]
    for station in stations.values():
        lines.append(
            f"  - {station['name']} (ID:{station['station_id']}, "
            f"容量:{station['capacity_kw']}kW, 位置:{station['location']})"
        )
    return "\n".join(lines)


def _format_station_selection(
    station_name: str,
    candidates: list[dict],
) -> str:
    """把站点歧义交给 Agent，避免业务层自行等待和解析用户回复。"""

    return json.dumps(
        {
            "status": "blocked",
            "code": "STATION_SELECTION_REQUIRED",
            "message": f"“{station_name}”匹配到多个站点，请先向用户确认具体站点。",
            "retryable": True,
            "data": station_selection_payload(station_name, candidates),
        },
        ensure_ascii=False,
    )


@tool
def list_all_stations() -> str:
    """查询系统当前已接入的全部有效光伏站点。"""
    return _format_station_list(load_stations_from_db())


@tool
def get_station_info(
    station_name: Annotated[
        str,
        "站点名称、关键词或站点ID。传空字符串 '' 则返回全部站点列表",
    ] = "",
) -> str:
    """查询光伏站点的名称、ID、经纬度、容量和位置。"""
    stations = load_stations_from_db()
    if not station_name.strip():
        return _format_station_list(stations)

    candidates = station_resolver.find_candidates(station_name, stations=stations)
    if len(candidates) > 1:
        station_resolver.mark_selection_pending(candidates)
        return _format_station_selection(station_name, candidates)

    info = resolve_station(station_name, stations=stations)
    if info is None:
        raise ToolException(
            f"未找到站点: '{station_name}'。当前可用站点: {list(stations.keys())}"
        )
    return (
        f"站点: {info['name']}\n"
        f"站点ID: {info['station_id']}\n"
        f"经纬度: lat={info['lat']}, lon={info['lon']}\n"
        f"装机容量: {info['capacity_kw']} kW\n"
        f"位置: {info['location']}"
    )


@tool
def get_station_location(
    station_name: Annotated[str, "站点名称、关键词或站点ID，例如 '英杰'"],
) -> str:
    """查询站点位置、经纬度和装机容量。"""
    stations = load_stations_from_db()
    candidates = station_resolver.find_candidates(station_name, stations=stations)
    if len(candidates) > 1:
        station_resolver.mark_selection_pending(candidates)
        return _format_station_selection(station_name, candidates)
    info = resolve_station(station_name, stations=stations)
    if info is not None:
        print(
            f"✅ 匹配到站点: {info['name']} "
            f"(lat={info['lat']}, lon={info['lon']})"
        )
        return (
            f"站点: {info['name']} (ID:{info['station_id']}) | "
            f"经纬度: {info['lat']},{info['lon']} | "
            f"装机: {info['capacity_kw']}kW"
        )

    raise ToolException(
        f"未找到站点 '{station_name}'。当前可用站点: {list(stations.keys())}"
    )


@tool
def get_stations_by_region(
    region: str,
    max_stations: int = REGION_QUERY_MAX_STATIONS,
) -> str:
    """查询省、市或地区下的有效光伏站点，不要求用户从中选择一个。"""
    if max_stations < 1 or max_stations > REGION_QUERY_MAX_STATIONS:
        raise ToolException(
            f"max_stations 必须在 1 到 {REGION_QUERY_MAX_STATIONS} 之间"
        )

    result = station_scope_resolver.resolve_region(
        region,
        max_stations=max_stations,
    )
    return json.dumps(result, ensure_ascii=False)
