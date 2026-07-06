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

# todo 站点信息（后续可改为从数据库 solar_station 表读取）
STATIONS = {
    "英杰": {
        "station_id": "2003021982752313403",
        "name": "宁波海曙英杰250KW光伏",
        "lat": 29.78,
        "lon": 121.36,
        "capacity_kw": 250.0,
        "location": "浙江宁波",
    },
    # 后续多站点扩展时在此添加，或改为从数据库读取
}

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

    返回:
        站点信息的文字描述，包含经纬度、装机容量等。
    """
    # 模糊匹配: 站点名、站点ID、位置关键词都尝试
    for key, info in STATIONS.items():
        if (key in station_name or
            station_name in info["name"] or
            station_name == info["station_id"] or
            station_name in info.get("location", "")):
            print(f"✅ 匹配到站点: {info['name']} (lat={info['lat']}, lon={info['lon']})")
            return (
                f"站点: {info['name']}\n"
                f"站点ID: {info['station_id']}\n"
                f"经纬度: lat={info['lat']}, lon={info['lon']}\n"
                f"装机容量: {info['capacity_kw']} kW\n"
                f"位置: {info['location']}"
            )

    available = list(STATIONS.keys())
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
    loc_result = get_station_location.invoke({"station_name": "英杰"})
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

    print("\n" + "=" * 60)
    print("自测完成")
    print("=" * 60)
