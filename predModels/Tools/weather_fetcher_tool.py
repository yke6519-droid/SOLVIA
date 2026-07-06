"""
weather_fetcher_tool.py - 气象数据拉取工具模块 (LangChain Tools)
=========================================================
从 open-meteo API 拉取气象数据，所有公开函数均按 LangChain @tool 标准定义，
可直接绑定到 LangChain Agent 使用。

数据源:
  - 历史气象: https://archive-api.open-meteo.com/v1/archive
  - 未来气象: https://api.open-meteo.com/v1/forecast

工具列表:
  1. get_today_weather        - 获取当天 0-23 点未来气象 (content + DataFrame)
  2. get_yesterday_weather    - 获取昨天 0-23 点历史气象 (content + DataFrame)
  3. get_weather_by_range     - 获取指定日期范围气象 (content + DataFrame)
  4. get_station_location     - 根据站点名查经纬度 (文本)
  5. get_current_datetime     - 获取当前日期时间 (文本)

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

__all__ = [
    "get_today_weather",
    "get_yesterday_weather",
    "get_weather_by_range",
    "get_station_location",
    "get_current_datetime",
]

# ============================================================
# 配置区
# ============================================================

# open-meteo 请求的气象变量（与原预测脚本一致）
VARIABLES = [
    "temperature_2m",
    "dew_point_2m",
    "cloud_cover_low",
    "wind_speed_10m",
    "wind_direction_10m",
    "shortwave_radiation",
    "direct_radiation",
]

# API 地址
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"

# MySQL 连接配置（用于从 solar_station 表读取站点信息）
MYSQL_URL = "mysql+pymysql://root:755028@localhost:3306/solar_agent?charset=utf8mb4"


def _load_stations_from_db() -> dict:
    """
    从 MySQL solar_station 表加载所有启用的站点信息。

    替代原来硬编码的 STATIONS 字典。
    每次调用 get_station_location 时实时查库，保证数据最新。

    返回:
        dict: {站点关键词: {station_id, name, lat, lon, capacity_kw, location}}
              key 用站点名的核心词（去掉省市前缀和容量后缀），
              方便 LLM 用简称匹配，如 "英杰"、"哲丰新材料清水站"
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(MYSQL_URL)
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
        # 用字段名访问，避免下标搞错
        # row._mapping 是 SQLAlchemy Row 的字段名映射
        r = row._mapping
        full_name = r["name"]
        lat = float(r["latitude"]) if r["latitude"] is not None else None
        lon = float(r["longitude"]) if r["longitude"] is not None else None
        capacity = float(r["capacity_kw"]) if r["capacity_kw"] is not None else None

        # 提取站点简称作为 key（去掉省市前缀和容量后缀）
        # "宁波海曙英杰250KW光伏" → "英杰"
        # "哲丰新材料清水站新增" → "哲丰新材料清水站新增"
        short_name = _extract_short_name(full_name)

        stations[short_name] = {
            "station_id": str(r["id"]),       # 数据库主键 id
            "name": full_name,
            "lat": lat,
            "lon": lon,
            "capacity_kw": capacity,
            "location": r["location"] or "",  # location 字段
        }

    print(f"📋 从数据库加载 {len(stations)} 个站点: {list(stations.keys())}")
    return stations


def _extract_short_name(full_name: str) -> str:
    """
    从完整站点名中提取简称作为匹配 key。

    规则:
      "宁波海曙英杰250KW光伏"       → "英杰"       （去掉省市、容量、光伏）
      "哲丰新材料清水站新增"         → "哲丰新材料清水站新增"（无省市前缀，直接用）
      "浙江省-衢州市-哲丰3#造纸车间" → "哲丰3#造纸车间"（去掉"省-市-"前缀）

    匹配策略：get_station_location 会同时用简称和全名做模糊匹配，
    所以 key 提取不完美也没关系，全名也会参与匹配。
    """
    name = full_name

    # 去掉 "省-市-" 前缀格式
    if '-' in name:
        parts = name.split('-')
        if len(parts) >= 3:
            name = '-'.join(parts[2:])

    # 去掉容量标记 (250KW / 3MW / 400kWp 等)
    import re
    name = re.sub(r'\d+(?:\.\d+)?\s*(KW|kw|Kw|kWp|KWp|MW|mw|Mw|MWp)', '', name, flags=re.IGNORECASE)

    # 去掉尾部"光伏""分布式电站""新增"等通用后缀
    name = re.sub(r'(光伏|分布式电站|新增)$', '', name).strip()

    return name if name else full_name


def _match_station(station_name: str, stations: dict) -> dict:
    """
    模糊匹配站点：支持简称、全名、站点ID、位置关键词多种匹配方式。

    参数:
        station_name: 用户输入的站点名/关键词/ID
        stations: _load_stations_from_db() 返回的站点字典

    返回:
        dict: 匹配到的站点信息，未匹配返回 None
    """
    for key, info in stations.items():
        if (key in station_name or
            station_name in info["name"] or
            station_name == info["station_id"] or
            station_name in info.get("location", "")):
            return info
    return None

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


def _fetch_from_archive(lat, lon, start_date, end_date):
    """
    调用 archive API 拉取历史气象。
    内部函数，返回 DataFrame。
    """
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
    return df


def _fetch_from_forecast(lat, lon, start_date, end_date):
    """
    调用 forecast API 拉取未来气象。
    内部函数，返回 DataFrame。
    """
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

    return "\n".join(lines)


# ============================================================
# LangChain Tools
# ============================================================

@tool(response_format="content_and_artifact")
def get_today_weather(
    lat: Annotated[float, "纬度，例如 29.78"],
    lon: Annotated[float, "经度，例如 121.36"],
) -> tuple[str, pd.DataFrame]:
    """获取指定经纬度当天 0-23 点的未来气象预报数据。

    返回 24 行小时级数据，包含温度、露点、低云量、短波辐射、直接辐射、风分量等字段。
    适用于需要当天气象条件的场景，例如光伏发电预测。
    数据来源为 open-meteo 预报 API (forecast)。

    返回:
        content: 气象数据文字摘要（供 LLM 阅读）
        artifact: 原始 DataFrame（供下游工具如 pv_predictor 使用）
    """
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"📥 拉取今天({today})未来气象: lat={lat}, lon={lon}")

    try:
        df = _fetch_from_forecast(lat, lon, today, today)
        df = df.head(24).reset_index(drop=True)
        print(f"✅ 今天气象拉取完成: {len(df)} 条")
        summary = _format_weather_summary(df, f"今天({today})未来气象")
        return summary, df
    except Exception as e:
        print(f"❌ 今天气象拉取失败: {e}")
        raise ToolException(f"今天({today})气象拉取失败: {e}")


@tool(response_format="content_and_artifact")
def get_yesterday_weather(
    lat: Annotated[float, "纬度，例如 29.78"],
    lon: Annotated[float, "经度，例如 121.36"],
) -> tuple[str, pd.DataFrame]:
    """获取指定经纬度昨天 0-23 点的历史气象数据。

    返回 24 行小时级数据，字段与 get_today_weather 一致。
    适用于需要历史气象作为预测模型输入基准的场景。
    数据来源为 open-meteo 历史 API (archive)。

    返回:
        content: 气象数据文字摘要（供 LLM 阅读）
        artifact: 原始 DataFrame（供下游工具使用）
    """
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"📥 拉取昨天({yesterday})历史气象: lat={lat}, lon={lon}")

    try:
        df = _fetch_from_archive(lat, lon, yesterday, yesterday)
        df = df.head(24).reset_index(drop=True)
        print(f"✅ 昨天气象拉取完成: {len(df)} 条")
        summary = _format_weather_summary(df, f"昨天({yesterday})历史气象")
        return summary, df
    except Exception as e:
        print(f"❌ 昨天气象拉取失败: {e}")
        raise ToolException(f"昨天({yesterday})气象拉取失败: {e}")


@tool(response_format="content_and_artifact")
def get_weather_by_range(
    lat: Annotated[float, "纬度，例如 29.78"],
    lon: Annotated[float, "经度，例如 121.36"],
    start_date: Annotated[str, "起始日期，格式 YYYY-MM-DD，例如 2026-06-30"],
    end_date: Annotated[str, "结束日期，格式 YYYY-MM-DD，例如 2026-07-06"],
) -> tuple[str, pd.DataFrame]:
    """获取指定经纬度在指定日期范围内的气象数据。

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
    return summary, result


@tool
def get_station_location(
    station_name: Annotated[str, "站点名称、关键词或站点ID，例如 '英杰'"],
) -> str:
    """根据站点名称、关键词或站点ID查询站点的位置信息。

    支持模糊匹配：站点简称、站点全名、站点ID、位置关键词均可作为输入。
    返回站点ID、名称、经纬度、装机容量、位置描述等信息。
    其他天气工具需要的 lat/lon 参数可通过本工具获取。
    站点数据从 MySQL solar_station 表实时读取。

    返回:
        站点信息的文字描述，包含经纬度、装机容量等。
    """
    # 从数据库加载所有站点
    stations = _load_stations_from_db()
    info = _match_station(station_name, stations)

    if info is not None:
        print(f"✅ 匹配到站点: {info['name']} (lat={info['lat']}, lon={info['lon']})")
        return (
            f"站点: {info['name']}\n"
            f"站点ID: {info['station_id']}\n"
            f"经纬度: lat={info['lat']}, lon={info['lon']}\n"
            f"装机容量: {info['capacity_kw']} kW\n"
            f"位置: {info['location']}"
        )

    available = list(stations.keys())
    print(f"❌ 未找到站点: {station_name}")
    print(f"   当前可用站点: {available}")
    raise ToolException(
        f"未找到站点: '{station_name}'。当前可用站点: {available}"
    )


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


# ============================================================
# 全流程测试函数
# ============================================================

def full_flow_test(station_name: str):
    """
    全流程测试：输入站点名称 → 查经纬度 → 拉今日+昨日气象 → 生成摘要。

    模拟 Agent 实际调用链:
      1. get_station_location("英杰") → 拿到 lat/lon
      2. get_today_weather(lat, lon)  → 今日气象
      3. get_yesterday_weather(lat, lon) → 昨日气象
      4. 汇总摘要
    """
    print(f"  输入站点名: {station_name}")
    print(f"  {'─' * 56}")

    # 步骤1: 查站点信息
    print(f"  [步骤1] 查询站点信息...")
    try:
        loc_str = get_station_location.invoke({"station_name": station_name})
        print(f"  {loc_str}")
    except Exception as e:
        print(f"  ❌ 站点查询失败: {e}")
        return

    # 从数据库拿结构化数据（工具返回的是文字摘要，这里需要 lat/lon）
    stations = _load_stations_from_db()
    info = _match_station(station_name, stations)
    if info is None or info["lat"] is None or info["lon"] is None:
        print(f"  ⚠️ 站点经纬度未设置，无法拉取气象数据（请先在数据库补充经纬度）")
        return

    lat, lon = info["lat"], info["lon"]
    print(f"\n  [步骤2] 拉取今日气象 (lat={lat}, lon={lon})...")
    today_result = get_today_weather.invoke({"lat": lat, "lon": lon})
    if hasattr(today_result, "content"):
        print(f"  ✅ 今日气象摘要:\n{today_result.content}")
        df_today = today_result.artifact
    else:
        print(f"  ✅ {today_result}")
        df_today = None

    print(f"\n  [步骤3] 拉取昨日气象 (lat={lat}, lon={lon})...")
    yesterday_result = get_yesterday_weather.invoke({"lat": lat, "lon": lon})
    if hasattr(yesterday_result, "content"):
        print(f"  ✅ 昨日气象摘要:\n{yesterday_result.content}")
        df_yesterday = yesterday_result.artifact
    else:
        print(f"  ✅ {yesterday_result}")
        df_yesterday = None

    # 步骤4: 汇总摘要
    print(f"\n  [步骤4] 全流程摘要")
    print(f"  {'─' * 56}")
    print(f"  站点: {info['name']} (ID: {info['station_id']})")
    print(f"  经纬度: lat={lat}, lon={lon}")
    print(f"  装机容量: {info['capacity_kw']} kW")

    if df_today is not None and df_yesterday is not None:
        # 对比今日 vs 昨日的辐射和温度
        today_ssrd = df_today["shortwave_radiation"].mean()
        yest_ssrd = df_yesterday["shortwave_radiation"].mean()
        today_temp = df_today["temperature_2m"].mean()
        yest_temp = df_yesterday["temperature_2m"].mean()

        ssrd_change = ((today_ssrd - yest_ssrd) / max(yest_ssrd, 0.1)) * 100
        temp_change = today_temp - yest_temp

        print(f"  {'指标':<20} {'昨日':>10} {'今日':>10} {'变化':>10}")
        print(f"  {'-' * 52}")
        print(f"  {'平均短波辐射(W/m²)':<18} {yest_ssrd:>10.1f} {today_ssrd:>10.1f} {ssrd_change:>+9.1f}%")
        print(f"  {'平均温度(°C)':<20} {yest_temp:>10.1f} {today_temp:>10.1f} {temp_change:>+9.1f}°C")
        print(f"  {'数据条数':<20} {len(df_yesterday):>10} {len(df_today):>10}")

    print(f"  {'─' * 56}")
    print(f"  ✅ 全流程测试完成")


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("weather_fetcher_tool.py 模块自测 (LangChain @tool)")
    print("=" * 60)

    # 测试 1: 获取当前时间
    print("\n--- 测试 1: get_current_datetime ---")
    dt_result = get_current_datetime.invoke({})
    print(f"  类型: {type(dt_result)}")
    print(f"  结果:\n{dt_result}")

    # 测试 2: 站点查询
    print("\n--- 测试 2: get_station_location ---")
    loc_result = get_station_location.invoke({"station_name": "哲丰新材料九号机"})
    print(f"  类型: {type(loc_result)}")
    print(f"  结果:\n{loc_result}")

    # 测试 3: 昨天气象 (content + artifact)
    print("\n--- 测试 3: get_yesterday_weather ---")
    y_result = get_yesterday_weather.invoke({"lat": 29.78, "lon": 121.36})
    print(f"  类型: {type(y_result)}")
    # content_and_artifact 工具 invoke 返回 ToolMessage
    if hasattr(y_result, "content") and hasattr(y_result, "artifact"):
        print(f"  content (LLM 摘要):\n{y_result.content}")
        df_y = y_result.artifact
        print(f"  artifact (DataFrame): shape={df_y.shape}")
        print(f"  列: {list(df_y.columns)}")
        print(f"  时间范围: {df_y['time'].min()} ~ {df_y['time'].max()}")
    else:
        print(f"  结果: {y_result}")

    # 测试 4: 今天气象 (content + artifact)
    print("\n--- 测试 4: get_today_weather ---")
    t_result = get_today_weather.invoke({"lat": 29.78, "lon": 121.36})
    print(f"  类型: {type(t_result)}")
    if hasattr(t_result, "content") and hasattr(t_result, "artifact"):
        print(f"  content (LLM 摘要):\n{t_result.content}")
        df_t = t_result.artifact
        print(f"  artifact (DataFrame): shape={df_t.shape}")
        print(f"  时间范围: {df_t['time'].min()} ~ {df_t['time'].max()}")
    else:
        print(f"  结果: {t_result}")

    # 测试 5: 日期范围查询（昨天到今天）
    print("\n--- 测试 5: get_weather_by_range ---")
    r_result = get_weather_by_range.invoke({
        "lat": 29.78,
        "lon": 121.36,
        "start_date": "2026-07-05",
        "end_date": "2026-07-06",
    })
    print(f"  类型: {type(r_result)}")
    if hasattr(r_result, "content") and hasattr(r_result, "artifact"):
        print(f"  content (LLM 摘要):\n{r_result.content}")
        df_r = r_result.artifact
        print(f"  artifact (DataFrame): shape={df_r.shape}")
        print(f"  时间范围: {df_r['time'].min()} ~ {df_r['time'].max()}")
    else:
        print(f"  结果: {r_result}")

    # 测试 6: 异常处理（不存在的站点）
    print("\n--- 测试 6: get_station_location (异常) ---")
    try:
        bad_result = get_station_location.invoke({"station_name": "不存在的站点"})
        print(f"  结果: {bad_result}")
    except Exception as e:
        print(f"  预期异常: {type(e).__name__}: {e}")

    # 测试 7: 全流程（站点名 → 经纬度 → 今日+昨日气象 → 摘要）
    print("\n--- 测试 7: 全流程（站点名 → 气象数据 → 摘要） ---")
    full_flow_test("英杰")

    print("\n" + "=" * 60)
    print("自测完成")
    print("=" * 60)
