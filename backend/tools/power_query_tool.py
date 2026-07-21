"""
power_query_tool.py - 数据库查询工具模块 (LangChain Tools)
============================================================
统一管理所有数据库查询操作,供 LLM 和其他工具模块复用。

设计原则:
  - 查询工具只管"读",不管"写"。写入(缓存入库)留在 cache_manager 和各工具内部
  - 内部函数(下划线前缀)供模块间复用,@tool(无下划线)暴露给 LLM
  - cache_manager 是缓存层(读+写+清理),本模块是查询层(只读),职责不混

工具列表(@tool,暴露给 LLM):
  1. get_station_info            查询站点信息(空则返回全部)
  2. get_actual_power            查询单日实际发电量
  3. get_actual_power_by_range   查询日期范围实际发电量
  4. get_predicted_power         查询预测发电量(从缓存读)
  5. get_weather_records         查询气象数据(从缓存读)
  6. get_power_comparison        预测vs实际对比(JOIN查询)

内部函数(供其他模块复用):
  _query_station_by_name         按名称模糊匹配站点(weather_fetcher 复用)
  _query_actual_power            查实际发电量(pv_predictor 复用)

TODO:
  - 发电量异常检测:聚合统计(和前7天均值对比),后续按需实现
"""
import pandas as pd
from typing import Annotated
from sqlalchemy import text
from langchain_core.tools import tool, ToolException
from dotenv import load_dotenv
import os
load_dotenv()

from backend.tools.cache_manager import get_prediction_data_mode
from backend.app.database import get_engine

# MySQL 连接配置(与其他模块一致)
MYSQL_URL = os.environ.get("MYSQL_URL")


# ============================================================
# 内部函数(供其他模块复用)
# ============================================================

def _query_station_by_name(station_name: str = None) -> dict:
    """
    从 MySQL solar_station 表加载站点信息,支持模糊匹配。

    由 weather_fetcher_tool.get_station_location 调用,替代原来的 _load_stations_from_db。
    返回格式与原函数完全一致,保证向后兼容。

    参数:
        station_name: 站点名称关键词。None 则返回全部站点。

    返回:
        dict: {站点简称: {station_id, name, lat, lon, capacity_kw, location}}
              未匹配到则返回空 dict
    """
    engine = get_engine()
    query = text("""
        SELECT id, station_code, name, capacity_kw, location, province, city,
               longitude, latitude
        FROM solar_station
        WHERE status = 1
    """)

    stations = {}
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    for row in rows:
        r = row._mapping
        full_name = r["name"]
        lat = float(r["latitude"]) if r["latitude"] is not None else None
        lon = float(r["longitude"]) if r["longitude"] is not None else None
        capacity = float(r["capacity_kw"]) if r["capacity_kw"] is not None else None

        # 提取站点简称作为 key(复用 weather_fetcher 的逻辑)
        from backend.tools.weather_fetcher_tool import _extract_short_name, _match_station
        short_name = _extract_short_name(full_name)

        stations[short_name] = {
            "station_id": str(r["id"]),
            "name": full_name,
            "lat": lat,
            "lon": lon,
            "capacity_kw": capacity,
            "location": r["location"] or "",
            "province": r["province"] or "",
            "city": r["city"] or "",
        }

    print(f"📋 从数据库加载 {len(stations)} 个站点: {list(stations.keys())}")

    # 如果传了站点名,做模糊匹配返回单个
    if station_name is not None:
        from backend.tools.weather_fetcher_tool import _match_station
        matched = _match_station(station_name, stations)
        return matched if matched else {}

    return stations


def _query_stations_by_region(
    *,
    region_keyword: str,
    limit: int | None = None,
) -> list[dict]:
    """Query all active stations matching a region keyword.

    This is deliberately a database-level collection query.  It does not go
    through ``_match_station`` because a region is a requested set of sites,
    not an ambiguous single-site name that needs user selection.
    """
    keyword = str(region_keyword or "").strip()
    if not keyword:
        raise ValueError("region_keyword is required")

    engine = get_engine()
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
        + " ORDER BY id"
        + limit_clause
    )

    with engine.connect() as conn:
        rows = conn.execute(query, params).fetchall()

    stations: list[dict] = []
    for row in rows:
        item = row._mapping
        stations.append(
            {
                "station_id": str(item["id"]),
                "name": item["name"],
                "capacity_kw": (
                    float(item["capacity_kw"])
                    if item["capacity_kw"] is not None
                    else None
                ),
                "province": item["province"] or "",
                "city": item["city"] or "",
                "location": item["location"] or "",
                "lat": (
                    float(item["latitude"])
                    if item["latitude"] is not None
                    else None
                ),
                "lon": (
                    float(item["longitude"])
                    if item["longitude"] is not None
                    else None
                ),
            }
        )
    return stations


def _query_actual_power(station_id: str, predict_date: str) -> pd.DataFrame:
    """
    查询某站点某天的实际发电量。

    由 pv_predictor.compare_with_actual 调用,替代原来内嵌的 SQL 查询。

    参数:
        station_id: 站点ID
        predict_date: 日期 "YYYY-MM-DD"

    返回:
        DataFrame(record_time, power_kwh),无数据则返回空 DataFrame
    """
    engine = get_engine()
    query = text("""
        SELECT record_time, power_kwh
        FROM power_generation
        WHERE station_id = :sid
          AND DATE(record_time) = :dt
        ORDER BY record_time
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"sid": station_id, "dt": predict_date})
    return df


def _query_actual_power_range(station_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    查询某站点日期范围内的全部原始发电量记录(逐小时)。

    由 table_io_tool._fetch_data 调用,用于范围导出 Excel。
    不做天汇总,返回每条原始记录,供 LLM 和用户做自定义分析。

    参数:
        station_id: 站点ID
        start_date: 起始日期 "YYYY-MM-DD"
        end_date: 结束日期 "YYYY-MM-DD"

    返回:
        DataFrame(record_time, power_kwh),无数据则返回空 DataFrame
    """
    engine = get_engine()
    query = text("""
        SELECT record_time, power_kwh
        FROM power_generation
        WHERE station_id = :sid
          AND DATE(record_time) BETWEEN :start AND :end
        ORDER BY record_time
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"sid": station_id, "start": start_date, "end": end_date})
    return df


# ============================================================
# 辅助函数
# ============================================================

def _resolve_station_id(station_name: str) -> tuple:
    """
    通过站点名查 station_id 和完整信息。

    参数:
        station_name: 站点名称/关键词/ID

    返回:
        tuple: (station_id, station_info_dict)
        未匹配则抛 ToolException
    """
    stations = _query_station_by_name()
    from backend.tools.weather_fetcher_tool import _match_station
    info = _match_station(station_name, stations)
    if info is None:
        available = list(stations.keys())
        raise ToolException(
            f"未找到站点: '{station_name}'。当前可用站点: {available}"
        )
    return info["station_id"], info


def _format_power_summary(df: pd.DataFrame, label: str) -> str:
    """把发电量 DataFrame 转成 LLM 可读摘要"""
    if len(df) == 0:
        return f"{label}: 无数据"
    total = df["power_kwh"].sum()
    peak_row = df.loc[df["power_kwh"].idxmax()]
    peak_time = pd.to_datetime(peak_row["record_time"]).strftime("%H:%M") if "record_time" in df.columns else "?"
    peak_val = float(peak_row["power_kwh"])
    nonzero = len(df[df["power_kwh"] > 0])
    lines = [
        f"{label}:",
        f"  总发电量: {total:.1f} kWh",
        f"  峰值时段: {peak_time}, 峰值发电: {peak_val:.1f} kWh",
        f"  有效发电时段: {nonzero} 小时",
    ]
    return "\n".join(lines)


# ============================================================
# LangChain Tools (@tool, 暴露给 LLM)
# ============================================================

@tool
def get_station_info(
    station_name: Annotated[str, "站点名称、关键词或站点ID。传空字符串 '' 则返回全部站点列表"] = "",
) -> str:
    """查询光伏站点信息。

    支持模糊匹配:站点简称、全名、站点ID、位置关键词均可作为输入。
    返回站点ID、名称、经纬度、装机容量、位置描述等信息。
    传空字符串则返回所有可用站点列表。
    站点数据从 MySQL solar_station 表实时读取。

    返回:
        站点信息的文字描述。多个站点时返回列表。
    """
    if station_name.strip() == "":
        # 返回全部站点
        stations = _query_station_by_name()
        lines = [f"共 {len(stations)} 个可用站点:"]
        for key, info in stations.items():
            lines.append(
                f"  - {info['name']} (ID:{info['station_id']}, "
                f"容量:{info['capacity_kw']}kW, 位置:{info['location']})"
            )
        return "\n".join(lines)

    # 查单个站点
    station_id, info = _resolve_station_id(station_name)
    return (
        f"站点: {info['name']}\n"
        f"站点ID: {info['station_id']}\n"
        f"经纬度: lat={info['lat']}, lon={info['lon']}\n"
        f"装机容量: {info['capacity_kw']} kW\n"
        f"位置: {info['location']}"
    )


@tool
def get_actual_power(
    station_name: Annotated[str, "站点名称,例如 '英杰'"],
    target_date: Annotated[str, "日期,支持 'YYYY-MM-DD'、'M月D日'、'M月D号' 等格式"],
) -> str:
    """查询某站点指定日期的实际发电量(24小时数据)。

    数据来源为 MySQL power_generation 表,是电站真实发电记录。
    返回该日24小时的发电量明细和统计摘要。

    返回:
        实际发电量的文字摘要(总发电量、峰值时段、有效发电时长)
    """
    from backend.tools.date_parser_tool import parse_flexible_date
    predict_date = parse_flexible_date(target_date)
    station_id, info = _resolve_station_id(station_name)

    df = _query_actual_power(station_id, predict_date)
    if len(df) == 0:
        return f"⏳ {info['name']} 在 {predict_date} 暂无实际发电量数据(可能尚未入库)"

    df["record_time"] = pd.to_datetime(df["record_time"])
    summary = _format_power_summary(df, f"{info['name']} {predict_date} 实际发电量")
    return summary


@tool
def get_actual_power_by_range(
    station_name: Annotated[str, "站点名称,例如 '英杰'"],
    start_date: Annotated[str, "起始日期,支持 'YYYY-MM-DD'、'M月D日' 等格式"],
    end_date: Annotated[str, "结束日期,支持 'YYYY-MM-DD'、'M月D日' 等格式"],
) -> str:
    """查询某站点指定日期范围的实际发电量(按天汇总)。

    返回每天的发电量总计,适合查看发电量趋势。
    数据来源为 MySQL power_generation 表。

    返回:
        每日发电量的文字摘要列表
    """
    from backend.tools.date_parser_tool import parse_flexible_date
    start = parse_flexible_date(start_date)
    end = parse_flexible_date(end_date)
    station_id, info = _resolve_station_id(station_name)

    engine = get_engine()
    query = text("""
        SELECT DATE(record_time) as date, SUM(power_kwh) as daily_total
        FROM power_generation
        WHERE station_id = :sid
          AND DATE(record_time) BETWEEN :start AND :end
        GROUP BY DATE(record_time)
        ORDER BY date
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"sid": station_id, "start": start, "end": end})

    if len(df) == 0:
        return f"⏳ {info['name']} 在 {start} ~ {end} 暂无实际发电量数据"

    lines = [f"{info['name']} {start} ~ {end} 每日发电量:"]
    for _, row in df.iterrows():
        lines.append(f"  {row['date']}: {row['daily_total']:.1f} kWh")
    avg = df["daily_total"].mean()
    lines.append(f"  日均发电量: {avg:.1f} kWh")
    return "\n".join(lines)


@tool
def get_predicted_power(
    station_name: Annotated[str, "站点名称,例如 '英杰'"],
    target_date: Annotated[str, "日期,支持 'YYYY-MM-DD'、'M月D日' 等格式"],
) -> str:
    """查询某站点指定日期的预测发电量。

    数据来源为 prediction_cache 缓存表(预测结果缓存,3小时内有效)。
    如果该日期没有预测记录,提示用户先调用 predict_power 生成预测。

    返回:
        预测发电量的文字摘要(总发电量、峰值时段、天气类型)
    """
    from backend.tools.date_parser_tool import parse_flexible_date
    from backend.tools.cache_manager import read_prediction_cache
    predict_date = parse_flexible_date(target_date)
    station_id, info = _resolve_station_id(station_name)

    df = read_prediction_cache(
        station_id, predict_date, get_prediction_data_mode(predict_date)
    )
    if df is None:
        return (
            f"⏳ {info['name']} 在 {predict_date} 暂无预测缓存记录。\n"
            f"请先调用 predict_power 工具生成该日期的预测。"
        )

    total = df["fusion"].sum()
    peak_row = df.loc[df["fusion"].idxmax()]
    peak_time = pd.to_datetime(peak_row["time"]).strftime("%H:%M")
    peak_val = float(peak_row["fusion"])
    weather = df["weather_type"].iloc[0] if "weather_type" in df.columns else "未知"

    return (
        f"{info['name']} {predict_date} 预测发电量:\n"
        f"  总发电量: {total:.1f} kWh\n"
        f"  峰值时段: {peak_time}, 峰值发电: {peak_val:.1f} kWh\n"
        f"  天气类型: {weather}"
    )


@tool
def get_weather_records(
    station_name: Annotated[str, "站点名称,例如 '英杰'"],
    target_date: Annotated[str, "日期,支持 'YYYY-MM-DD'、'M月D日' 等格式"],
    data_type: Annotated[str, "数据类型: 'forecast'=未来气象预报, 'archive'=历史气象实况"] = "archive",
) -> str:
    """查询某站点指定日期的气象数据。

    数据来源为气象缓存表(weather_forecast_cache / weather_archive_cache)。
    - data_type='archive': 查历史气象实况(永久缓存)
    - data_type='forecast': 查未来气象预报(短期缓存)
    如果缓存中没有数据,提示用户先调用天气工具拉取。

    返回:
        气象数据的文字摘要(温度、辐射、云量等范围和均值)
    """
    from backend.tools.date_parser_tool import parse_flexible_date
    from backend.tools.cache_manager import read_archive_cache, read_forecast_cache
    predict_date = parse_flexible_date(target_date)
    station_id, info = _resolve_station_id(station_name)

    if data_type == "forecast":
        df = read_forecast_cache(station_id, predict_date)
        label = "未来气象预报"
    else:
        df = read_archive_cache(station_id, predict_date)
        label = "历史气象实况"

    if df is None:
        return (
            f"⏳ {info['name']} 在 {predict_date} 暂无{label}缓存数据。\n"
            f"请先调用 get_weather_by_range 拉取气象数据。"
        )

    lines = [f"{info['name']} {predict_date} {label} (共 {len(df)} 小时):"]
    col_specs = [
        ("temperature_2m", "温度", "%.1f°C"),
        ("dew_point_2m", "露点", "%.1f°C"),
        ("cloud_cover_low", "低云量", "%.0f%%"),
        ("shortwave_radiation", "短波辐射", "%.1f W/m²"),
        ("direct_radiation", "直接辐射", "%.1f W/m²"),
    ]
    for col, name, fmt in col_specs:
        if col in df.columns:
            s = df[col]
            lines.append(f"  {name}: {fmt % s.min()} ~ {fmt % s.max()}, 均值 {fmt % s.mean()}")
    return "\n".join(lines)


@tool
def get_power_comparison(
    station_name: Annotated[str, "站点名称,例如 '英杰'"],
    target_date: Annotated[str, "日期,支持 'YYYY-MM-DD'、'M月D日' 等格式"],
) -> str:
    """查询某站点指定日期的预测vs实际发电量对比。

    JOIN prediction_cache 和 power_generation 两张表,一次查询返回对比结果。
    适合分析"预测准不准"。
    如果预测或实际数据任一缺失,会提示用户。

    返回:
        对比摘要(预测总量、实际总量、偏差、偏差率、逐时对比)
    """
    from backend.tools.date_parser_tool import parse_flexible_date
    from backend.tools.cache_manager import read_prediction_cache
    predict_date = parse_flexible_date(target_date)
    station_id, info = _resolve_station_id(station_name)

    # 查预测缓存
    pred_df = read_prediction_cache(
        station_id, predict_date, get_prediction_data_mode(predict_date)
    )
    if pred_df is None:
        return (
            f"⏳ {info['name']} 在 {predict_date} 暂无预测记录。\n"
            f"请先调用 predict_power 生成预测。"
        )

    # 查实际发电量
    actual_df = _query_actual_power(station_id, predict_date)
    if len(actual_df) == 0:
        return (
            f"⏳ {info['name']} 在 {predict_date} 暂无实际发电量数据(可能尚未入库)。\n"
            f"预测总发电量: {pred_df['fusion'].sum():.1f} kWh"
        )

    # 对齐时间做对比
    pred_df = pred_df.copy()
    pred_df["hour"] = pd.to_datetime(pred_df["time"]).dt.hour
    actual_df["hour"] = pd.to_datetime(actual_df["record_time"]).dt.hour

    merged = pd.merge(
        pred_df[["hour", "fusion"]],
        actual_df[["hour", "power_kwh"]],
        on="hour",
        how="inner"
    )

    if len(merged) == 0:
        return "⚠️ 预测与实际数据时间无法对齐"

    pred_total = merged["fusion"].sum()
    actual_total = merged["power_kwh"].sum()
    deviation = pred_total - actual_total
    deviation_rate = (deviation / actual_total * 100) if actual_total > 0 else 0

    # 逐时最大偏差
    merged["diff"] = (merged["fusion"] - merged["power_kwh"]).abs()
    max_diff_row = merged.loc[merged["diff"].idxmax()]

    lines = [
        f"{info['name']} {predict_date} 预测 vs 实际对比:",
        f"  预测总发电量: {pred_total:.1f} kWh",
        f"  实际总发电量: {actual_total:.1f} kWh",
        f"  偏差: {deviation:+.1f} kWh ({deviation_rate:+.1f}%)",
        f"  最大逐时偏差: {int(max_diff_row['hour']):02d}:00, 差 {max_diff_row['diff']:.1f} kWh",
        f"  对齐小时数: {len(merged)} / 24",
    ]
    return "\n".join(lines)


# ============================================================
# TODO: 发电量异常检测(后续按需实现)
# ============================================================
# get_power_anomaly: 聚合统计,和前7天均值对比,找出异常低的发电日
# 实现思路:
#   1. 查最近 N 天的每日总发电量
#   2. 计算移动平均和标准差
#   3. 超出 2 倍标准差的标记为异常
# 触发场景:用户问"哪些站点今天发电异常?"时才需要
