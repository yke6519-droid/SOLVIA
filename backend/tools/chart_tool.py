"""
chart_tool.py - 发电量可视化数据工具 (LangChain Tool)
======================================================
提供发电量图表的结构化数据,供 Vue 前端使用 ECharts 渲染。

工具列表(@tool,暴露给 LLM):
  1. get_power_chart_data  获取发电量图表数据(返回结构化 JSON)

设计说明:
  - 复用 table_io_tool._fetch_data() 获取数据,不重复造轮子
  - 不生成文件、不画图,只返回结构化 JSON 字符串
  - JSON 结构兼容 ECharts: title / x_axis / series / metadata
  - 前端可直接 setOption() 渲染交互式折线图
"""
import json
import math
from datetime import datetime
from typing import Annotated

from langchain_core.tools import tool, ToolException


# 图表配色(预测蓝、实际橙、偏差红)
SERIES_COLORS = {
    "预测发电量(kWh)": "#2E86C1",
    "实际发电量(kWh)": "#E67E22",
}

# 数据类型 → 标题模板
TITLE_TEMPLATES = {
    "actual": "{station}站点{year}年{month}月{day}日发电量数据",
    "predicted": "{station}站点{year}年{month}月{day}日预测发电量数据",
    "comparison": "{station}站点{year}年{month}月{day}日预测vs实际发电量对比",
}


# ============================================================
# 内部辅助函数
# ============================================================

def _safe_round(value, digits=2, default=0.0):
    """安全转 float 并四舍五入,处理 NaN/None。"""
    if value is None:
        return default
    f = float(value)
    if math.isnan(f):
        return default
    return round(f, digits)


def _compute_stats(values, x_labels, label_prefix):
    """
    计算一个数值列的统计信息。

    返回 dict:
        {label_prefix}_total_kwh, {label_prefix}_peak_time,
        {label_prefix}_peak_value_kwh, {label_prefix}_generation_hours
    """
    total = round(sum(_safe_round(v) for v in values), 2)
    peak_idx = max(range(len(values)), key=lambda i: _safe_round(values[i]))
    peak_val = _safe_round(values[peak_idx])
    nonzero_hours = sum(1 for v in values if _safe_round(v) > 0)

    return {
        f"{label_prefix}_total_kwh": total,
        f"{label_prefix}_peak_time": x_labels[peak_idx],
        f"{label_prefix}_peak_value_kwh": peak_val,
        f"{label_prefix}_generation_hours": nonzero_hours,
    }


def _build_chart_data(df, data_type, info, predict_date):
    """
    将 _fetch_data 返回的 DataFrame 转为前端可消费的图表 JSON 结构。

    DataFrame 列(由 table_io_tool._fetch_data 产出):
      actual:      ["时间", "实际发电量(kWh)"]
      predicted:   ["时间", "预测发电量(kWh)"]
      comparison:  ["时间", "预测发电量(kWh)", "实际发电量(kWh)", "偏差(kWh)"]
    """
    from backend.app.services.station_catalog_service import extract_short_name

    # 日期组件
    dt = datetime.strptime(predict_date, "%Y-%m-%d")
    station_short = extract_short_name(info["name"])

    title = TITLE_TEMPLATES[data_type].format(
        station=station_short,
        year=dt.year,
        month=dt.month,
        day=dt.day,
    )

    # X 轴:时段 ["0:00", "1:00", ..., "23:00"]
    x_data = df["时间"].tolist()

    # 确定折线系列:排除"时间"和"偏差"列
    power_cols = [c for c in df.columns if c != "时间" and "偏差" not in c]

    series = []
    for col in power_cols:
        raw_values = df[col].tolist()
        series.append({
            "name": col,
            "color": SERIES_COLORS.get(col, "#95A5A6"),
            "data": [_safe_round(v) for v in raw_values],
        })

    # 元数据
    metadata = {
        "station": station_short,
        "station_full_name": info["name"],
        "date": predict_date,
        "data_type": data_type,
    }

    # 各系列的统计信息
    for i, col in enumerate(power_cols):
        raw_values = df[col].tolist()
        label = "actual" if "实际" in col else "predicted"
        metadata.update(_compute_stats(raw_values, x_data, label))

    # 对比模式:补充偏差统计 + 逐时偏差数组
    if data_type == "comparison" and "偏差(kWh)" in df.columns:
        diff_values = df["偏差(kWh)"].tolist()
        diff_safe = [_safe_round(v) for v in diff_values]
        metadata["diff_data"] = diff_safe

        pred_total = metadata.get("predicted_total_kwh", 0)
        actual_total = metadata.get("actual_total_kwh", 0)
        deviation = round(pred_total - actual_total, 2)
        deviation_rate = round(deviation / actual_total * 100, 2) if actual_total > 0 else 0

        metadata["deviation_kwh"] = deviation
        metadata["deviation_rate_pct"] = deviation_rate

        # 最大偏差时段
        max_diff_idx = max(range(len(diff_safe)), key=lambda i: abs(diff_safe[i]))
        metadata["max_diff_time"] = x_data[max_diff_idx]
        metadata["max_diff_value"] = diff_safe[max_diff_idx]

    return {
        "chart_type": "line",
        "title": title,
        "x_axis": {
            "label": "时段",
            "data": x_data,
        },
        "y_axis": {
            "label": "发电量(kWh)",
        },
        "series": series,
        "metadata": metadata,
    }


# ============================================================
# LangChain Tool (@tool, 暴露给 LLM)
# ============================================================

@tool
def get_power_chart_data(
    station_name: Annotated[str, "站点名称,如 '英杰'"],
    target_date: Annotated[str, "日期,支持 'YYYY-MM-DD'、'7月13日'、'7.13'、'后天' 等"],
    data_type: Annotated[str, "数据类型: 'actual'=实际发电量, 'predicted'=预测发电量, 'comparison'=预测vs实际对比"],
) -> str:
    """获取发电量可视化图表数据(返回结构化JSON)。

    支持场景:用户想看发电量折线图、可视化图表时调用。
    返回前端 ECharts 可直接消费的 JSON 结构(title/x_axis/series/metadata)。
    返回:
        JSON 字符串,包含图表标题、X轴时段、Y轴数据系列、统计元数据
    """
    from backend.tools.date_parser_tool import parse_flexible_date
    from backend.tools.power_query_tool import _resolve_station_id
    from backend.tools.table_io_tool import _fetch_data, DATA_TYPE_LABELS

    # 校验 data_type
    if data_type not in TITLE_TEMPLATES:
        raise ToolException(
            f"不支持的数据类型: '{data_type}'。支持: actual, predicted, comparison"
        )

    # 解析日期和站点
    predict_date = parse_flexible_date(target_date)
    station_id, info = _resolve_station_id(station_name)

    # 获取数据(复用 table_io_tool 的数据获取逻辑)
    df = _fetch_data(data_type, station_id, info, predict_date, station_name)

    if len(df) == 0:
        raise ToolException(
            f"未获取到数据: {info['name']} {predict_date} {DATA_TYPE_LABELS.get(data_type, data_type)}"
        )

    # 构建图表 JSON
    chart_data = _build_chart_data(df, data_type, info, predict_date)

    return json.dumps(chart_data, ensure_ascii=False, indent=2)

# ============================================================
# Range chart support
# ============================================================

def _load_range_power_frames(station_id: str, start_date: str, end_date: str, data_type: str):
    """Load actual/predicted hourly frames for a date range."""
    import pandas as pd
    from backend.tools.cache_manager import get_prediction_data_mode, read_prediction_cache
    from backend.tools.power_query_tool import _query_actual_power_range

    frames = {}
    if data_type in {"actual", "comparison"}:
        actual = _query_actual_power_range(station_id, start_date, end_date)
        if len(actual):
            # 数据库字段在图表边界统一为 timestamp/value_kwh。
            actual = actual.rename(columns={"record_time": "timestamp", "power_kwh": "value_kwh"})
            actual["timestamp"] = pd.to_datetime(actual["timestamp"])
            actual["value_kwh"] = pd.to_numeric(actual["value_kwh"], errors="coerce").fillna(0.0)
            frames["actual"] = actual[["timestamp", "value_kwh"]].sort_values("timestamp")

    if data_type in {"predicted", "comparison"}:
        predicted_parts = []
        for day in pd.date_range(start_date, end_date, freq="D"):
            day_string = day.strftime("%Y-%m-%d")
            predicted = read_prediction_cache(
                station_id,
                day_string,
                get_prediction_data_mode(day_string),
            )
            if predicted is None or len(predicted) == 0:
                continue
            # prediction_cache 的 time/fusion 只在模型缓存边界存在。
            predicted = predicted.rename(columns={"time": "timestamp", "fusion": "value_kwh"})
            predicted["timestamp"] = pd.to_datetime(predicted["timestamp"])
            predicted["value_kwh"] = pd.to_numeric(predicted["value_kwh"], errors="coerce").fillna(0.0)
            predicted_parts.append(predicted[["timestamp", "value_kwh"]])
        if predicted_parts:
            frames["predicted"] = pd.concat(predicted_parts, ignore_index=True).sort_values("timestamp")

    return frames


def _aggregate_range_frame(frame, granularity: str):
    import pandas as pd

    if frame is None or len(frame) == 0:
        return {}
    work = frame.copy()
    if granularity == "daily":
        work["label"] = work["timestamp"].dt.strftime("%Y-%m-%d")
    else:
        work["label"] = work["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
    grouped = work.groupby("label", sort=True)["value_kwh"].sum()
    return {str(label): _safe_round(value) for label, value in grouped.items()}


def _range_series_stats(values, labels, prefix):
    if not values:
        return {}
    peak_index = max(range(len(values)), key=lambda index: values[index])
    return {
        f"{prefix}_total_kwh": _safe_round(sum(values)),
        f"{prefix}_peak_time": labels[peak_index],
        f"{prefix}_peak_value_kwh": _safe_round(values[peak_index]),
        f"{prefix}_generation_hours": sum(1 for value in values if value > 0),
    }


def _build_range_chart_data(frames, station_info, start_date, end_date, data_type, granularity):
    import pandas as pd

    series_order = [name for name in ("predicted", "actual") if name in frames]
    if not series_order:
        raise ToolException(
            f"{station_info['name']} {start_date} 至 {end_date} 没有可用的发电量数据"
        )

    label_maps = {name: _aggregate_range_frame(frames[name], granularity) for name in series_order}
    labels = sorted({label for values in label_maps.values() for label in values})
    if not labels:
        raise ToolException(
            f"{station_info['name']} {start_date} 至 {end_date} 没有可用的发电量数据"
        )

    colors = {
        "actual": "#E67E22",
        "predicted": "#2E86C1",
    }
    names = {
        "actual": "实际发电量(kWh)",
        "predicted": "预测发电量(kWh)",
    }
    series = []
    metadata = {
        "station": station_info.get("name", ""),
        "station_full_name": station_info.get("name", ""),
        "start_date": start_date,
        "end_date": end_date,
        "range_start": start_date,
        "range_end": end_date,
        "date": start_date if start_date == end_date else None,
        "data_type": data_type,
        "granularity": granularity,
        "point_count": len(labels),
        "day_count": (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days + 1,
    }

    daily_totals = {}
    for name in series_order:
        values = [label_maps[name].get(label, 0.0) for label in labels]
        series.append({"name": names[name], "color": colors[name], "data": values})
        metadata.update(_range_series_stats(values, labels, name))
        if granularity == "daily":
            for label, value in label_maps[name].items():
                daily_totals.setdefault(label, {})[f"{name}_total_kwh"] = value

    if daily_totals:
        metadata["daily_totals"] = [
            {"date": label, **daily_totals[label]}
            for label in sorted(daily_totals)
        ]

    title_type = {
        "actual": "实际发电量",
        "predicted": "预测发电量",
        "comparison": "预测与实际发电量对比",
    }[data_type]
    granularity_label = "逐小时" if granularity == "hourly" else "按日汇总"

    return {
        "chart_type": "line",
        "title": f"{station_info.get('name', '')}{start_date} 至 {end_date}{granularity_label}{title_type}",
        "x_axis": {
            "label": "时间" if granularity == "hourly" else "日期",
            "data": labels,
        },
        "y_axis": {"label": "发电量(kWh)"},
        "series": series,
        "metadata": metadata,
    }


@tool
def get_power_chart_data_by_range(
    station_name: Annotated[str, "站点名称，例如 '英杰'"],
    start_date: Annotated[str, "开始日期，支持 YYYY-MM-DD、6月1日、6.1、后天等格式"],
    end_date: Annotated[str, "结束日期，支持 YYYY-MM-DD、6月3日、6.3、后天等格式"],
    data_type: Annotated[str, "数据类型：actual=实际，predicted=预测，comparison=预测与实际对比"] = "actual",
    granularity: Annotated[str, "粒度：hourly=逐小时，daily=按日汇总，auto=1到5天逐小时、超过5天按日汇总"] = "auto",
) -> str:
    """返回日期范围内可直接供前端渲染的结构化图表 JSON。"""
    import pandas as pd
    from backend.tools.date_parser_tool import parse_flexible_date
    from backend.tools.power_query_tool import _resolve_station_id

    if data_type not in {"actual", "predicted", "comparison"}:
        raise ToolException("data_type 只支持 actual、predicted、comparison")
    if granularity not in {"hourly", "daily", "auto"}:
        raise ToolException("granularity 只支持 hourly、daily、auto")

    start = parse_flexible_date(start_date)
    end = parse_flexible_date(end_date)
    
    if pd.to_datetime(start) > pd.to_datetime(end):
        start, end = end, start
    day_count = (pd.to_datetime(end) - pd.to_datetime(start)).days + 1
    resolved_granularity = granularity
    if resolved_granularity == "auto":
        resolved_granularity = "hourly" if day_count <= 5 else "daily"

    station_id, station_info = _resolve_station_id(station_name)
    frames = _load_range_power_frames(station_id, start, end, data_type)
    if data_type == "comparison" and not {"actual", "predicted"}.issubset(frames):
        missing = "实际" if "actual" not in frames else "预测"
        raise ToolException(f"{station_info['name']} {start} 至 {end} 缺少{missing}发电量数据，无法进行对比")

    chart_data = _build_range_chart_data(
        frames,
        station_info,
        start,
        end,
        data_type,
        resolved_granularity,
    )
    return json.dumps(chart_data, ensure_ascii=False, indent=2)
