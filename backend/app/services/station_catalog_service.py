"""站点目录服务。

这里集中负责 ``solar_station`` 表的读取、站点名称标准化和基础匹配。
它不负责 LangChain 工具注册，也不负责向用户提问；歧义结果由站点解析器
返回给 Agent，Agent 可调用的入口由 ``station_query_tool`` 提供。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from backend.app.database import get_engine

def extract_short_name(full_name: str) -> str:
    """从站点全名提取便于匹配的简称。"""
    name = str(full_name or "")

    # 去掉类似“浙江省-衢州市-”的地址前缀。
    if "-" in name:
        parts = name.split("-")
        if len(parts) >= 3:
            name = "-".join(parts[2:])

    # 去掉装机容量标记，例如 250KW、3MW、400kWp。
    name = re.sub(
        r"\d+(?:\.\d+)?\s*(KW|kw|Kw|kWp|KWp|MW|mw|Mw|MWp)",
        "",
        name,
        flags=re.IGNORECASE,
    )

    # 去掉常见业务后缀，但保留没有可提取简称时的原始名称。
    name = re.sub(r"(光伏|分布式电站|新增)$", "", name).strip()
    return name if name else str(full_name or "")


def load_stations_from_db() -> dict[str, dict[str, Any]]:
    """从 MySQL 加载所有启用站点，返回统一结构的站点目录。"""
    query = text(
        """
        SELECT id, station_code, name, capacity_kw, location, province, city,
               longitude, latitude
        FROM solar_station
        WHERE status = 1
        """
    )

    with get_engine().connect() as conn:
        rows = conn.execute(query).fetchall()

    stations: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = row._mapping
        full_name = item["name"]
        station = {
            "station_id": str(item["id"]),
            "station_code": item["station_code"],
            "name": full_name,
            "lat": float(item["latitude"]) if item["latitude"] is not None else None,
            "lon": float(item["longitude"]) if item["longitude"] is not None else None,
            "capacity_kw": (
                float(item["capacity_kw"])
                if item["capacity_kw"] is not None
                else None
            ),
            "location": item["location"] or "",
            "province": item["province"] or "",
            "city": item["city"] or "",
        }
        stations[extract_short_name(full_name)] = station

    print(f"📋 从数据库加载 {len(stations)} 个站点: {list(stations.keys())}")
    return stations


def match_all_stations(
    station_reference: str,
    stations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """按简称、全名、站点 ID 或位置关键词返回所有候选站点。"""
    reference = str(station_reference or "").strip()
    matches: list[dict[str, Any]] = []
    for short_name, station in stations.items():
        if (
            short_name in reference
            or reference in station["name"]
            or reference == station["station_id"]
            or reference in station.get("location", "")
        ):
            matches.append(station)
    return matches


def query_stations_by_region(
    *,
    region_keyword: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """按省、市、位置或站点名查询区域内的有效站点。"""
    keyword = str(region_keyword or "").strip()
    if not keyword:
        raise ValueError("region_keyword is required")

    params: dict[str, object] = {"region_pattern": f"%{keyword}%"}
    limit_clause = " LIMIT :limit" if limit is not None else ""
    if limit is not None:
        params["limit"] = int(limit)

    query = text(
        "SELECT id, name, capacity_kw, province, city, location, "
        "longitude, latitude "
        "FROM solar_station "
        "WHERE status = 1 "
        "AND (province LIKE :region_pattern "
        "OR city LIKE :region_pattern "
        "OR location LIKE :region_pattern "
        "OR name LIKE :region_pattern) "
        "ORDER BY id"
        + limit_clause
    )

    with get_engine().connect() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "station_id": str(row._mapping["id"]),
            "name": row._mapping["name"],
            "capacity_kw": (
                float(row._mapping["capacity_kw"])
                if row._mapping["capacity_kw"] is not None
                else None
            ),
            "province": row._mapping["province"] or "",
            "city": row._mapping["city"] or "",
            "location": row._mapping["location"] or "",
            "lat": (
                float(row._mapping["latitude"])
                if row._mapping["latitude"] is not None
                else None
            ),
            "lon": (
                float(row._mapping["longitude"])
                if row._mapping["longitude"] is not None
                else None
            ),
        }
        for row in rows
    ]
