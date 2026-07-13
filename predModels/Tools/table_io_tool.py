"""
table_io_tool.py - 表格导入导出工具模块 (LangChain Tools)
=========================================================
提供 Excel/CSV 文件的导出和读取能力,供 LLM 在对话中直接调用。

工具列表(@tool,暴露给 LLM):
  1. export_table  导出光伏数据为 Excel/CSV(内部自动查缓存,未命中则预测/拉取)
  2. read_table    读取 Excel/CSV 文件并返回数据摘要

公共函数(供前端 API 直接调用):
  - export_table_to_bytes(station_name, target_date, data_type, file_format, filename)
      生成表格字节流,前端直接下载(不落盘)
  - read_table_from_bytes(file_bytes, filename)
      从前端上传的字节流读取表格(不需落盘)

设计说明:
  - 方式 A:工具内部自动走"查缓存→未命中则调底层函数→导出"流程
  - LLM 只传查询参数(站点+日期+类型),不需要传数据内容
  - 调底层函数(predict_station_power / _fetch_from_archive 等),不调 @tool
  - 输出目录从 .env 的 FILE_DIR 读取(与 file_io_tool 共用)
  - 默认导出 xlsx,支持 csv,校验合法性
  - 路径安全:防止 ../ 路径穿越
  - 前端对接:支持内存字节流,无需落盘即可读写
"""
import os
import pandas as pd
from datetime import datetime
from typing import Annotated, Optional

from langchain_core.tools import tool, ToolException
from dotenv import load_dotenv

load_dotenv()

# 输出目录(与 file_io_tool 共用)
FILE_DIR = os.getenv("FILE_DIR", "")

# 允许的文件格式
ALLOWED_FORMATS = ("xlsx", "csv")

# 允许的文件后缀(读取时用)
ALLOWED_READ_EXTENSIONS = (".xlsx", ".xls", ".csv")

# data_type 中文标签(用于文件名和列名)
DATA_TYPE_LABELS = {
    "actual": "实际发电量",
    "predicted": "预测发电量",
    "comparison": "预测vs实际对比",
    "weather_archive": "历史气象",
    "weather_forecast": "预报气象",
}

# 气象列名映射(英文 → 中文)
WEATHER_COLUMN_MAP = {
    "time": "时间",
    "temperature_2m": "温度(°C)",
    "dew_point_2m": "露点(°C)",
    "cloud_cover_low": "低云量(%)",
    "cloud_cover_mid": "中云量(%)",
    "cloud_cover_high": "高云量(%)",
    "shortwave_radiation": "短波辐射(W/m²)",
    "direct_radiation": "直接辐射(W/m²)",
    "diffuse_radiation": "散射辐射(W/m²)",
    "wind_speed_10m": "风速(m/s)",
    "precipitation": "降水量(mm)",
    "relative_humidity_2m": "相对湿度(%)",
}


# ============================================================
# 内部辅助函数
# ============================================================

def _sanitize_filename(filename: str) -> str:
    """清理文件名中的非法字符,替换为下划线。"""
    for ch in r'\/\\:*?"<>|':
        filename = filename.replace(ch, "_")
    return filename.strip()


def _get_unique_filepath(filepath: str) -> str:
    """文件已存在时追加时间戳后缀避免覆盖。"""
    if not os.path.exists(filepath):
        return filepath
    base, ext = os.path.splitext(filepath)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{timestamp}{ext}"


def _validate_format(file_format: str) -> str:
    """校验文件格式合法性,返回小写格式。"""
    fmt = file_format.strip().lower()
    if fmt not in ALLOWED_FORMATS:
        raise ToolException(
            f"不支持的文件格式: '{file_format}'。支持的格式: {', '.join(ALLOWED_FORMATS)}"
        )
    return fmt


def _resolve_filename(
    filename: str,
    file_format: str,
    station_name: str,
    predict_date: str,
    data_type: str,
) -> str:
    """
    生成最终文件名(含后缀)。
    - 用户传了 filename:清理非法字符,确保后缀合法
    - 用户没传:按 站点_日期_类型.xlsx 自动生成
    """
    if filename.strip():
        name = _sanitize_filename(filename.strip())
        # 确保后缀合法:有后缀就用,没有就补默认
        if name.endswith(".xlsx"):
            return name
        elif name.endswith(".csv"):
            return name
        elif name.endswith(".xls"):
            return name + "x"  # .xls → .xlsx
        else:
            return name + f".{file_format}"
    else:
        label = DATA_TYPE_LABELS.get(data_type, "导出数据")
        date_str = predict_date.replace("-", "")
        return f"{station_name}_{date_str}_{label}.{file_format}"


def _list_files_in_dir() -> list:
    """列出输出目录下的所有文件名,供错误提示用。"""
    try:
        files = [f for f in os.listdir(FILE_DIR)
                 if os.path.isfile(os.path.join(FILE_DIR, f))]
        return sorted(files)
    except Exception:
        return []


def _format_time_column(df: pd.DataFrame, time_col: str, fmt: str = "%H:%M") -> pd.DataFrame:
    """把时间列格式化为指定格式的字符串。

    参数:
        df: 数据
        time_col: 时间列名
        fmt: strftime 格式字符串,默认 "%H:%M"
             单日导出用 "%Y-%m-%d %H:%M" 保留完整日期时间
    """
    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df[time_col] = df[time_col].dt.strftime(fmt)
    return df


def _rename_weather_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把气象 DataFrame 的英文列名映射为中文。"""
    df = df.copy()
    rename_map = {col: WEATHER_COLUMN_MAP.get(col, col) for col in df.columns}
    df.rename(columns=rename_map, inplace=True)
    return df


def _write_dataframe(df: pd.DataFrame, filepath: str, file_format: str):
    """根据格式写 DataFrame 到文件。"""
    try:
        if file_format == "csv":
            df.to_csv(filepath, index=False, encoding="utf-8-sig")
        elif file_format == "xlsx":
            df.to_excel(filepath, index=False, engine="openpyxl")
    except Exception as e:
        raise ToolException(f"文件写入失败: {e}")


# ============================================================
# LangChain Tools (@tool, 暴露给 LLM)
# ============================================================

@tool
def export_table(
    station_name: Annotated[str, "站点名称,如 '英杰'"],
    target_date: Annotated[str, "日期,支持 'YYYY-MM-DD'、'7月9日'、'今天' 等"],
    data_type: Annotated[str, "数据类型: 'actual'=实际发电量, 'predicted'=预测发电量, 'comparison'=预测vs实际对比, 'weather_archive'=历史气象, 'weather_forecast'=预报气象"],
    file_format: Annotated[str, "文件格式: 'xlsx' 或 'csv',默认 xlsx"] = "xlsx",
    filename: Annotated[str, "文件名(可选),不传则自动生成"] = "",
    end_date: Annotated[str, "结束日期(可选),支持 'YYYY-MM-DD'、'6月1日' 等。传入时 target_date 作为起始日期,导出日期范围内的全部原始逐小时数据。仅对 data_type='actual' 有效"] = "",
) -> str:
    """导出光伏数据为 Excel 或 CSV 文件。

    支持场景:用户要求导出、下载、保存表格数据时调用。
    内部自动查缓存,缓存未命中时自动预测/拉取数据,无需用户额外操作。
    默认导出 xlsx 格式,也可指定 csv。

    日期范围导出:
        当传入 end_date 时,target_date 作为起始日期,导出范围内全部原始逐小时数据。
        仅对 data_type='actual' 有效,其他类型忽略 end_date。

    返回:
        写入成功后的文件完整路径
    """
    if not FILE_DIR:
        raise ToolException("FILE_DIR 未配置,请在 .env 中设置 FILE_DIR")

    # 校验文件格式
    file_format = _validate_format(file_format)

    # 确保目录存在
    os.makedirs(FILE_DIR, exist_ok=True)

    # 解析站点和日期
    from predModels.Tools.power_query_tool import _resolve_station_id
    from predModels.Tools.date_parser_tool import parse_flexible_date
    station_id, info = _resolve_station_id(station_name)
    predict_date = parse_flexible_date(target_date)
    parsed_end_date = parse_flexible_date(end_date) if end_date.strip() else None

    # 根据数据类型获取 DataFrame
    df = _fetch_data(data_type, station_id, info, predict_date, station_name, parsed_end_date)

    if len(df) == 0:
        raise ToolException(f"未获取到数据: {info['name']} {predict_date} {DATA_TYPE_LABELS.get(data_type, data_type)}")

    # 生成文件名和路径
    date_for_name = f"{predict_date}_{parsed_end_date}" if parsed_end_date else predict_date
    final_name = _resolve_filename(filename, file_format, station_name, date_for_name, data_type)
    filepath = os.path.join(FILE_DIR, final_name)
    filepath = _get_unique_filepath(filepath)

    # 写入文件
    _write_dataframe(df, filepath, file_format)

    rows = len(df)
    cols = len(df.columns)
    return f"✅ 已导出 {rows} 行 × {cols} 列数据到文件: {filepath}"


@tool
def read_table(
    filename: Annotated[str, "要读取的文件名,如 '英杰7月9日预测.xlsx'"],
) -> str:
    """读取 Excel/CSV 文件并返回数据摘要。

    支持场景:用户要求查看之前导出的表格文件。
    返回行列数、列名、前 5 行预览、数值列统计信息。
    支持 .xlsx、.xls、.csv 格式。

    返回:
        文件数据的文字摘要(不全量返回,避免内容过长)
    """
    if not FILE_DIR:
        raise ToolException("FILE_DIR 未配置,请在 .env 中设置 FILE_DIR")

    # 路径安全:防止 ../ 路径穿越
    full_path = os.path.normpath(os.path.join(FILE_DIR, filename))
    if not full_path.startswith(os.path.normpath(FILE_DIR)):
        raise ToolException("文件名包含非法路径")

    # 检查文件是否存在
    if not os.path.exists(full_path):
        existing = _list_files_in_dir()
        if existing:
            hint = f"当前目录下有这些文件: {', '.join(existing)}"
        else:
            hint = "当前目录为空"
        return f"⏳ 文件不存在: {filename}\n{hint}"

    # 根据后缀选择读取引擎
    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext == ".csv":
            df = pd.read_csv(full_path, encoding="utf-8-sig")
        elif ext == ".xlsx":
            df = pd.read_excel(full_path, engine="openpyxl")
        elif ext == ".xls":
            df = pd.read_excel(full_path, engine="xlrd")
        else:
            raise ToolException(f"不支持的文件格式: {ext}。支持 .xlsx、.xls、.csv")
    except Exception as e:
        raise ToolException(f"文件读取失败: {e}")

    # 生成摘要
    return _generate_table_summary(df, filename)


# ============================================================
# 内部数据获取函数
# ============================================================

def _fetch_data(
    data_type: str,
    station_id: str,
    info: dict,
    predict_date: str,
    station_name: str,
    end_date: str = None,
) -> pd.DataFrame:
    """
    根据 data_type 获取 DataFrame。
    内部自动走"查缓存→未命中则调底层函数"流程。

    参数:
        end_date: 结束日期(可选),仅 data_type='actual' 时有效,
                  传入则导出日期范围内的全部原始逐小时数据
    """
    from predModels.Tools.cache_manager import (
        read_prediction_cache, read_archive_cache, read_forecast_cache,
    )
    from predModels.Tools.power_query_tool import _query_actual_power, _query_actual_power_range

    if data_type == "actual":
        if end_date:
            # 日期范围导出:全部原始逐小时数据
            df = _query_actual_power_range(station_id, predict_date, end_date)
            if len(df) == 0:
                return df
            df = _format_time_column(df, "record_time", fmt="%Y-%m-%d %H:%M")
            df.rename(columns={
                "record_time": "时间",
                "power_kwh": "实际发电量(kWh)",
            }, inplace=True)
            return df
        else:
            # 单日导出:24小时明细,保留完整日期时间
            df = _query_actual_power(station_id, predict_date)
            if len(df) == 0:
                return df
            df = _format_time_column(df, "record_time", fmt="%Y-%m-%d %H:%M")
            df.rename(columns={
                "record_time": "时间",
                "power_kwh": "实际发电量(kWh)",
            }, inplace=True)
            return df

    elif data_type == "predicted":
        # 预测发电量:先查缓存,未命中则调底层预测函数
        df = read_prediction_cache(station_id, predict_date)
        if df is None:
            from predModels.Tools.pv_predictor import predict_station_power
            lat = info["lat"]
            lon = info["lon"]
            summary, pred_df, weather_type = predict_station_power(
                station_name, lat, lon, station_id, predict_date
            )
            df = pred_df

        # 只取 time + fusion 列,重命名
        df = df[["time", "fusion"]].copy()
        df = _format_time_column(df, "time")
        weather_type = df.get("weather_type", pd.Series()).iloc[0] if "weather_type" in df.columns else ""
        df.rename(columns={
            "time": "时间",
            "fusion": "预测发电量(kWh)",
        }, inplace=True)
        return df

    elif data_type == "comparison":
        # 预测 vs 实际对比:两份数据 JOIN
        # 查预测(同 predicted 流程)
        pred_df = read_prediction_cache(station_id, predict_date)
        if pred_df is None:
            from predModels.Tools.pv_predictor import predict_station_power
            lat = info["lat"]
            lon = info["lon"]
            summary, pred_df, weather_type = predict_station_power(
                station_name, lat, lon, station_id, predict_date
            )

        # 查实际
        actual_df = _query_actual_power(station_id, predict_date)
        if len(actual_df) == 0:
            raise ToolException(
                f"{info['name']} 在 {predict_date} 暂无实际发电量数据,无法做对比导出"
            )

        # 按小时 JOIN
        pred_df = pred_df.copy()
        pred_df["hour"] = pd.to_datetime(pred_df["time"]).dt.hour
        actual_df = actual_df.copy()
        actual_df["hour"] = pd.to_datetime(actual_df["record_time"]).dt.hour

        merged = pd.merge(
            pred_df[["hour", "fusion"]],
            actual_df[["hour", "power_kwh"]],
            on="hour",
            how="inner"
        )
        merged["diff"] = merged["fusion"] - merged["power_kwh"]
        merged["hour_str"] = merged["hour"].apply(lambda h: f"{int(h):02d}:00")

        result = merged[["hour_str", "fusion", "power_kwh", "diff"]].copy()
        result.rename(columns={
            "hour_str": "时间",
            "fusion": "预测发电量(kWh)",
            "power_kwh": "实际发电量(kWh)",
            "diff": "偏差(kWh)",
        }, inplace=True)
        return result

    elif data_type == "weather_archive":
        # 历史气象:先查缓存,未命中则调底层拉取函数
        df = read_archive_cache(station_id, predict_date)
        if df is None:
            from predModels.Tools.weather_fetcher_tool import _fetch_from_archive
            lat = info["lat"]
            lon = info["lon"]
            df = _fetch_from_archive(lat, lon, predict_date, predict_date)
            if df is None or len(df) == 0:
                return pd.DataFrame()

        df = _format_time_column(df, "time")
        df = _rename_weather_columns(df)
        return df

    elif data_type == "weather_forecast":
        # 预报气象:先查缓存,未命中则调底层拉取函数
        df = read_forecast_cache(station_id, predict_date)
        if df is None:
            from predModels.Tools.weather_fetcher_tool import _fetch_from_forecast
            lat = info["lat"]
            lon = info["lon"]
            df = _fetch_from_forecast(lat, lon, predict_date, predict_date)
            if df is None or len(df) == 0:
                return pd.DataFrame()

        df = _format_time_column(df, "time")
        df = _rename_weather_columns(df)
        return df

    else:
        raise ToolException(
            f"不支持的数据类型: '{data_type}'。"
            f"支持: actual, predicted, comparison, weather_archive, weather_forecast"
        )


def _generate_table_summary(df: pd.DataFrame, filename: str) -> str:
    """生成表格数据的文字摘要(不全量返回,避免 token 爆炸)。"""
    rows, cols = df.shape
    lines = [
        f"📊 文件: {filename}",
        f"行列: {rows} 行 × {cols} 列",
        f"列名: {', '.join(str(c) for c in df.columns)}",
    ]

    # 前 5 行预览
    lines.append("前 5 行预览:")
    preview = df.head(5)
    for _, row in preview.iterrows():
        vals = "  ".join(f"{c}: {row[c]}" for c in df.columns)
        lines.append(f"  {vals}")

    # 数值列统计
    numeric_cols = df.select_dtypes(include=["number"]).columns
    if len(numeric_cols) > 0:
        lines.append("数值列统计:")
        for col in numeric_cols:
            lines.append(
                f"  {col}: {df[col].min():.1f} ~ {df[col].max():.1f}, 均值 {df[col].mean():.1f}"
            )

    return "\n".join(lines)


# ============================================================
# 公共函数(供前端 API 直接调用)
# ============================================================

def export_table_to_bytes(
    station_name: str,
    target_date: str,
    data_type: str,
    file_format: str = "xlsx",
    filename: str = "",
    end_date: str = "",
) -> dict:
    """
    生成表格字节流，供前端直接下载(不落盘)。

    前端对接流程:
      后端调本函数 → 返回 {filename, bytes, format, rows, cols}
      FastAPI 用 StreamingResponse 返回 bytes → 浏览器触发下载

    参数:
      station_name — 站点名称
      target_date  — 日期(支持自然语言)
      data_type    — 数据类型: actual/predicted/comparison/weather_archive/weather_forecast
      file_format  — xlsx 或 csv(默认 xlsx)
      filename     — 文件名(可选)
      end_date     — 结束日期(可选),传入时 target_date 作为起始日期,导出范围数据

    返回:
      {
        "filename": "英杰_20260713_实际发电量.xlsx",
        "bytes": b"...",
        "format": "xlsx",
        "rows": 24,
        "cols": 2
      }
    """
    import io

    file_format = _validate_format(file_format)

    # 解析站点和日期
    from predModels.Tools.power_query_tool import _resolve_station_id
    from predModels.Tools.date_parser_tool import parse_flexible_date
    station_id, info = _resolve_station_id(station_name)
    predict_date = parse_flexible_date(target_date)
    parsed_end_date = parse_flexible_date(end_date) if end_date.strip() else None

    # 获取 DataFrame
    df = _fetch_data(data_type, station_id, info, predict_date, station_name, parsed_end_date)

    if len(df) == 0:
        raise ToolException(f"未获取到数据: {info['name']} {predict_date} {DATA_TYPE_LABELS.get(data_type, data_type)}")

    # 生成文件名
    date_for_name = f"{predict_date}_{parsed_end_date}" if parsed_end_date else predict_date
    final_name = _resolve_filename(filename, file_format, station_name, date_for_name, data_type)

    # 写入内存字节流
    buffer = io.BytesIO()
    try:
        if file_format == "csv":
            df.to_csv(buffer, index=False, encoding="utf-8-sig")
        elif file_format == "xlsx":
            df.to_excel(buffer, index=False, engine="openpyxl")
    except Exception as e:
        raise ToolException(f"文件生成失败: {e}")

    return {
        "filename": final_name,
        "bytes": buffer.getvalue(),
        "format": file_format,
        "rows": len(df),
        "cols": len(df.columns),
    }


def read_table_from_bytes(
    file_bytes: bytes,
    filename: str,
) -> dict:
    """
    从前端上传的字节流读取表格(不需落盘)。

    前端对接流程:
      用户上传 Excel/CSV → FastAPI 拿到 file_bytes
      → 调本函数 → 返回 {filename, rows, cols, columns, preview, stats}

    参数:
      file_bytes — 文件字节流(前端上传)
      filename   — 文件名(用于判断格式)

    返回:
      {
        "filename": "英杰7月9日预测.xlsx",
        "rows": 24,
        "cols": 2,
        "columns": ["时间", "预测发电量(kWh)"],
        "preview": [{"时间": "00:00", "预测发电量(kWh)": 0.0}, ...],
        "stats": {"预测发电量(kWh)": {"min": 0.0, "max": 174.0, "mean": 54.7}}
      }
    """
    import io

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_READ_EXTENSIONS:
        raise ToolException(f"不支持的文件格式: {ext}。支持 {', '.join(ALLOWED_READ_EXTENSIONS)}")

    buffer = io.BytesIO(file_bytes)

    try:
        if ext == ".csv":
            df = pd.read_csv(buffer, encoding="utf-8-sig")
        elif ext == ".xlsx":
            df = pd.read_excel(buffer, engine="openpyxl")
        elif ext == ".xls":
            df = pd.read_excel(buffer, engine="xlrd")
    except Exception as e:
        raise ToolException(f"文件读取失败: {e}")

    # 前 5 行预览(转为 dict 列表，JSON 可序列化)
    preview_records = []
    for _, row in df.head(5).iterrows():
        record = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                record[col] = None
            elif hasattr(val, "isoformat"):
                record[col] = val.isoformat()
            else:
                record[col] = val
        preview_records.append(record)

    # 数值列统计
    stats = {}
    numeric_cols = df.select_dtypes(include=["number"]).columns
    for col in numeric_cols:
        stats[str(col)] = {
            "min": round(float(df[col].min()), 2),
            "max": round(float(df[col].max()), 2),
            "mean": round(float(df[col].mean()), 2),
        }

    return {
        "filename": filename,
        "rows": len(df),
        "cols": len(df.columns),
        "columns": [str(c) for c in df.columns],
        "preview": preview_records,
        "stats": stats,
    }
