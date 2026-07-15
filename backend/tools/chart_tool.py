"""
chart_tool.py - 发电量可视化数据工具 (LangChain Tool)
======================================================
提供发电量图表的结构化数据,供前端 ECharts 渲染或 CLI 下 LLM 呈现。

工具列表(@tool,暴露给 LLM):
  1. get_power_chart_data  获取发电量图表数据(返回结构化 JSON)

设计说明:
  - 复用 table_io_tool._fetch_data() 获取数据,不重复造轮子
  - 不生成文件、不画图,只返回结构化 JSON 字符串
  - JSON 结构兼容 ECharts: title / x_axis / series / metadata
  - 前端可直接 setOption() 渲染交互式折线图
  - CLI 环境下 LLM 可根据返回数据用表格/文字呈现
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
    from backend.tools.weather_fetcher_tool import _extract_short_name

    # 日期组件
    dt = datetime.strptime(predict_date, "%Y-%m-%d")
    station_short = _extract_short_name(info["name"])

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
    target_date: Annotated[str, "日期,支持 'YYYY-MM-DD'、'7月13日'、'今天' 等"],
    data_type: Annotated[str, "数据类型: 'actual'=实际发电量, 'predicted'=预测发电量, 'comparison'=预测vs实际对比"],
) -> str:
    """获取发电量可视化图表数据(返回结构化JSON)。

    支持场景:用户想看发电量折线图、可视化图表时调用。
    返回前端 ECharts 可直接消费的 JSON 结构(title/x_axis/series/metadata)。
    CLI 环境下可根据返回数据用表格或文字呈现关键信息。

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
