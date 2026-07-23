"""
weather_fetcher_tool.py - 气象数据拉取工具模块 (LangChain Tools)
=========================================================
从 open-meteo API 拉取气象数据。

数据源:
  - 历史气象: https://archive-api.open-meteo.com/v1/archive
  - 未来气象: https://api.open-meteo.com/v1/forecast

工具列表(@tool, 暴露给 LLM):
  1. get_weather_by_range     - 获取指定日期范围气象 (content + DataFrame)
  2. get_current_datetime     - 获取当前日期时间 (文本)

内部函数(非 @tool, 供 pv_predictor 等模块调用):
  - fetch_weather_by_date     - 按指定日期拉取单日气象(自动判断 archive/forecast)
  - _fetch_from_archive       - 调 archive API 拉历史气象
  - _fetch_from_forecast      - 调 forecast API 拉未来气象

设计说明:
  - 返回 DataFrame 的工具使用 response_format="content_and_artifact"，
    LLM 看到的是文字摘要 (content)，下游工具可拿到原始 DataFrame (artifact)。
  - 参数描述使用 Annotated[type, "描述"]，自动生成 tool schema 供 LLM 参考。
  - 异常通过 ToolException 抛出，Agent 可捕获并重试或告知用户。
"""
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timedelta
from typing import Annotated
from langchain_core.tools import tool, ToolException
import os
from dotenv import load_dotenv
load_dotenv()

__all__ = [
    "fetch_weather_by_date",
    "get_weather_by_range",
    "get_current_datetime",
]

# ============================================================
# 配置区
# ============================================================

# open-meteo 请求的气象变量。
#
# 注意：新增的三个字段只用于天气查询和分析。
# pv_predictor.convert_to_era5_format() 仍然通过原有 target_cols 白名单选取模型特征，
# 因此这些字段不会进入预测模型。
VARIABLES = [
    "temperature_2m",
    "dew_point_2m",
    "cloud_cover_low",
    "wind_speed_10m",
    "wind_direction_10m",
    "shortwave_radiation",
    "direct_radiation",
    "precipitation",
    "sunshine_duration",
]

# API 地址
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"

# MySQL 连接配置（用于从 solar_station 表读取站点信息）
MYSQL_URL = os.getenv("MYSQL_URL")


# 兼容旧的内部导入：真正的站点目录逻辑已集中到 Service。
# 这些别名不再承担数据库查询或匹配业务，只是避免预测、图表等旧模块立刻失效。
from backend.app.services.station_catalog_service import (
    extract_short_name as _extract_short_name,
    load_stations_from_db as _load_stations_from_db,
    match_all_stations as _match_all_stations,
)


def _match_station(station_name: str, stations: dict) -> dict | None:
    """兼容旧调用，统一交给任务级 StationResolver 处理歧义。"""
    from backend.app.services.station_resolver import resolve_station

    return resolve_station(station_name, stations=stations)

# ============================================================
# 内部工具函数（非 Tool，供 Tool 内部调用）
# ============================================================

def _parse_wind_components(df):
    """
    将风速+风向转为 U/V 分量，并删除原始列。
    从原脚本原样搬移，不改逻辑。
    """
    wind_dir_rad = np.radians(df['wind_direction_10m'])
    df['10m_u_component_of_wind'] = df['wind_speed_10m'] * np.sin(wind_dir_rad)
    df['10m_v_component_of_wind'] = df['wind_speed_10m'] * np.cos(wind_dir_rad)
    df.drop(columns=['wind_speed_10m', 'wind_direction_10m'], inplace=True)
    return df


def _fetch_from_archive(lat, lon, start_date, end_date, station_id=None):
    """
    调用 archive API 拉取历史气象。
    内部函数，返回 DataFrame。

    参数:
        lat, lon: 经纬度
        start_date, end_date: 日期范围 "YYYY-MM-DD"
        station_id: 站点ID(可选)。传入则启用历史气象缓存(永久保存)，
                    未命中才调 API，拉完写入缓存；不传则直接调 API。
    """
    # 【缓存查询】历史气象永久保存，过去天气不会变
    if station_id is not None:
        from backend.tools.cache_manager import read_archive_cache, write_archive_cache
        # 单日查询直接走缓存
        if start_date == end_date:
            print("有缓存")
            cached = read_archive_cache(station_id, start_date)
            if cached is not None:
                return cached

    # 【API 拉取】缓存未命中或不启用缓存，走原始 API
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(VARIABLES),
        "timezone": "Asia/Shanghai",
    }
    response = requests.get(ARCHIVE_API, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    df = pd.DataFrame(data["hourly"])
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values("time").reset_index(drop=True)
    df = _parse_wind_components(df)

    # 【缓存写入】拉到的历史气象写入缓存(永久保存)
    if station_id is not None:
        try:
            from backend.tools.cache_manager import write_archive_cache
            write_archive_cache(station_id, df)
        except Exception as e:
            print(f"⚠️ 历史气象缓存写入失败(不影响功能): {e}")

    return df


def _fetch_from_forecast(lat, lon, start_date, end_date, station_id=None):
    """
    调用 forecast API 拉取未来气象。
    内部函数，返回 DataFrame。

    参数:
        lat, lon: 经纬度
        start_date, end_date: 日期范围 "YYYY-MM-DD"
        station_id: 站点ID(可选)。传入则启用未来气象缓存(短期有效)，
                    未命中才调 API，拉完写入缓存；不传则直接调 API。
    """
    # 【缓存查询】未来气象短期有效，过期的会被惰性清理
    if station_id is not None:
        from backend.tools.cache_manager import read_forecast_cache, write_forecast_cache
        if start_date == end_date:
            cached = read_forecast_cache(station_id, start_date)
            if cached is not None:
                return cached

    # 【API 拉取】缓存未命中或不启用缓存，走原始 API
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(VARIABLES),
        "timezone": "Asia/Shanghai",
        "start_date": start_date,
        "end_date": end_date,
    }
    response = requests.get(FORECAST_API, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    df = pd.DataFrame(data["hourly"])
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values("time").reset_index(drop=True)
    df = _parse_wind_components(df)

    # 【缓存写入】拉到的未来气象写入缓存(短期有效，过期自动清理)
    if station_id is not None:
        try:
            from backend.tools.cache_manager import write_forecast_cache
            write_forecast_cache(station_id, df)
        except Exception as e:
            print(f"⚠️ 未来气象缓存写入失败(不影响功能): {e}")

    return df


def _format_weather_summary(df: pd.DataFrame, label: str = "气象数据") -> str:
    """
    将气象 DataFrame 转为 LLM 可读的文字摘要。
    LLM 通过此摘要了解数据概况，无需读取完整表格。
    """
    lines = [f"成功获取{label}，共 {len(df)} 条（{len(df)} 小时）。"]
    lines.append(f"时间范围: {df['time'].min()} ~ {df['time'].max()}")

    col_specs = [
        ("temperature_2m",     "温度(temperature_2m)",       "°C",    "%.1f"),
        ("dew_point_2m",       "露点(dew_point_2m)",         "°C",    "%.1f"),
        ("cloud_cover_low",    "低云量(cloud_cover_low)",    "%",     "%.0f"),
        ("shortwave_radiation","短波辐射(shortwave_radiation)","W/m²", "%.1f"),
        ("direct_radiation",   "直接辐射(direct_radiation)",  "W/m²", "%.1f"),
    ]
    for col, label_str, unit, fmt in col_specs:
        if col in df.columns:
            s = df[col]
            lines.append(f"{label_str}: {fmt % s.min()} ~ {fmt % s.max()} {unit}，均值 {fmt % s.mean()} {unit}")

    # 分析字段单独汇总，避免把它们混入预测特征说明。
    if "precipitation" in df.columns:
        precipitation = pd.to_numeric(df["precipitation"], errors="coerce").dropna()
        if not precipitation.empty:
            lines.append(
                f"总降水量: {precipitation.sum():.1f} mm，"
                f"最大单小时降水量: {precipitation.max():.1f} mm，"
                f"有降水时段: {(precipitation > 0).sum()} 小时"
            )

    if "sunshine_duration" in df.columns:
        sunshine = pd.to_numeric(df["sunshine_duration"], errors="coerce").dropna()
        if not sunshine.empty:
            lines.append(f"有效日照时长: {sunshine.sum() / 3600:.1f} 小时")

    return "\n".join(lines)


# ============================================================
# LangChain Tools
# ============================================================
def fetch_weather_by_date(
    lat: float,
    lon: float,
    date_str: str,
    station_id: str = None,
) -> pd.DataFrame:
    """按指定日期拉取单日 24h 气象数据（内部函数，非 @tool）。

    自动判断使用 archive API 还是 forecast API:
    - 过去日期（昨天及更早）→ archive API（历史实况）
    - 今天及未来日期 → forecast API（预报）

    参数:
        lat, lon: 经纬度
        date_str: 日期 "YYYY-MM-DD"
        station_id: 站点ID（可选），传入则启用缓存

    返回:
        pd.DataFrame: 24 行小时级气象数据
    """
    today = datetime.now().date()
    target = datetime.strptime(date_str, "%Y-%m-%d").date()

    if target < today:
        df = _fetch_from_archive(lat, lon, date_str, date_str, station_id)
    else:
        df = _fetch_from_forecast(lat, lon, date_str, date_str, station_id)

    return df.head(24).reset_index(drop=True)


@tool(response_format="content_and_artifact")
def get_weather_by_range(
    lat: Annotated[float, "纬度，例如 29.78"],
    lon: Annotated[float, "经度，例如 121.36"],
    start_date: Annotated[str, "起始日期，格式 YYYY-MM-DD，例如 2026-06-30"],
    end_date: Annotated[str, "结束日期，格式 YYYY-MM-DD，例如 2026-07-06"],
) -> tuple[str, pd.DataFrame]:
    """获取指定经纬度在指定日期范围内的气象数据。

    用户需要查询单日的天气时，让start_date = end_date即可

    自动判断使用历史 API 还是预报 API：
    - 过去日期（昨天及更早）使用 archive API（历史实况）
    - 今天及未来日期使用 forecast API（预报）
    - 跨今天边界时自动分段调用并拼接

    每行一小时数据，按时间升序排列。
    适用于查询任意时段气象的场景。

    返回:
        content: 气象数据文字摘要（供 LLM 阅读）
        artifact: 原始 DataFrame（供下游工具使用）
    """
    today = datetime.now().date()
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    if start > end:
        raise ToolException(
            f"起始日期({start_date})不能晚于结束日期({end_date})"
        )

    print(f"📥 拉取气象数据: {start_date} ~ {end_date}, lat={lat}, lon={lon}")

    # 昨天及之前 → archive，今天及之后 → forecast
    archive_end = min(end, today - timedelta(days=1))
    forecast_start = max(start, today)

    frames = []

    # 历史段
    if start <= archive_end:
        hist_start = start_date
        hist_end = archive_end.strftime("%Y-%m-%d")
        try:
            print(f"   历史段: {hist_start} ~ {hist_end} (archive API)")
            df_hist = _fetch_from_archive(lat, lon, hist_start, hist_end)
            frames.append(df_hist)
        except Exception as e:
            print(f"❌ 历史段拉取失败: {e}")

    # 未来段
    if forecast_start <= end:
        fc_start = forecast_start.strftime("%Y-%m-%d")
        fc_end = end_date
        try:
            print(f"   未来段: {fc_start} ~ {fc_end} (forecast API)")
            df_fc = _fetch_from_forecast(lat, lon, fc_start, fc_end)
            frames.append(df_fc)
        except Exception as e:
            print(f"❌ 未来段拉取失败: {e}")

    if not frames:
        raise ToolException(
            f"气象数据拉取失败: {start_date} ~ {end_date}，所有分段均失败"
        )

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values("time").reset_index(drop=True)
    print(f"✅ 气象数据拉取完成: {len(result)} 条, "
          f"{result['time'].min()} ~ {result['time'].max()}")

    summary = _format_weather_summary(
        result, f"{start_date} ~ {end_date} 气象数据"
    )
    # 天气查询同样登记为通用数据制品，后续可以被导出或分析复用。
    from backend.app.services.dataset_artifact_service import (
        create_dataset_artifact,
        dataset_reference,
    )
    import json

    artifact_frame = result.copy()
    artifact_frame["source_type"] = "weather"
    artifact_frame["series_key"] = "weather"
    dataset = create_dataset_artifact(
        artifact_frame,
        artifact_type="tabular",
        source_tool="get_weather_by_range",
        metadata={
            "data_type": "weather",
            "source_type": "weather",
            "period_start": start_date,
            "period_end": end_date,
            "granularity": "hourly",
            "latitude": lat,
            "longitude": lon,
        },
    )
    summary = (
        f"{summary}\n\n数据制品已生成："
        f"{json.dumps(dataset_reference(dataset), ensure_ascii=False)}"
    )
    return summary, result


@tool
def get_current_datetime() -> str:
    """获取当前日期时间信息。

    返回当前日期、时间、星期几、今天日期和昨天日期。
    用于确定时间基准，辅助判断该用历史 API 还是预报 API。
    调用天气工具前可先用本工具确认当前时间。

    返回:
        当前时间信息的文字描述。
    """
    now = datetime.now()
    weekdays_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekdays_en = ["Monday", "Tuesday", "Wednesday", "Thursday",
                   "Friday", "Saturday", "Sunday"]

    wd_idx = now.weekday()  # 0=Monday
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    return (
        f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')} {weekdays_cn[wd_idx]} ({weekdays_en[wd_idx]})\n"
        f"今天: {today}\n"
        f"昨天: {yesterday}\n"
        f"时间戳: {int(now.timestamp())}"
    )
