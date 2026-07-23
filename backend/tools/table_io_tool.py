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
import json
import pandas as pd
from datetime import datetime, timedelta
from typing import Annotated, Optional

from langchain_core.tools import tool, ToolException
from dotenv import load_dotenv
from backend.app.services.dataset_artifact_service import (
    DatasetArtifactError,
    resolve_dataset_for_export,
)
from backend.app.services.attachment_service import resolve_bound_attachment_path
from backend.app.services.file_artifact_service import register_current_generated_file

load_dotenv()

# 输出目录(与 file_io_tool 共用)
FILE_DIR = os.getenv("FILE_DIR", "")

# 允许的文件格式
ALLOWED_FORMATS = ("xlsx", "csv")

# 允许的文件后缀(读取时用)
ALLOWED_READ_EXTENSIONS = (".xlsx", ".xls", ".csv")

# 对话中展示完整工作簿摘要时，避免异常大的工作簿把 Agent 上下文撑爆。
# 这里限制的是 Sheet 数量，不限制真实文件导入；导入逻辑仍由 import_tool 负责。
MAX_SUMMARY_SHEETS = 20

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
    "precipitation": "总降水量(mm)",
    "sunshine_duration": "有效日照时长(秒)",
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


def _dataframe_to_bytes(df: pd.DataFrame, file_format: str) -> bytes:
    """把标准化 DataFrame 编码成下载字节流，供 API 层直接返回。"""
    import io

    buffer = io.BytesIO()
    try:
        if file_format == "csv":
            df.to_csv(buffer, index=False, encoding="utf-8-sig")
        elif file_format == "xlsx":
            df.to_excel(buffer, index=False, engine="openpyxl")
        else:
            raise ToolException(f"不支持的文件格式: {file_format}")
    except ToolException:
        raise
    except Exception as exc:
        raise ToolException(f"文件生成失败: {exc}") from exc
    return buffer.getvalue()


def _artifact_to_dataframe(artifact) -> pd.DataFrame:
    """把 DatasetArtifact 的标准化 rows 转成可导出的 DataFrame。

    导出器不关心数据来自实际、预测、天气还是多站点对比；只消费
    artifact 的统一 rows/schema。列顺序优先使用 schema 声明顺序，
    未声明的扩展列放到末尾，避免不同数据制品导出时列顺序漂移。
    """
    if not artifact.rows:
        return pd.DataFrame()

    frame = pd.DataFrame(artifact.rows)
    schema_names = list(artifact.field_schema.keys())
    ordered = [name for name in schema_names if name in frame.columns]
    ordered.extend(name for name in frame.columns if name not in ordered)
    frame = frame.loc[:, ordered]

    # DatasetArtifact 的内部协议使用长表，便于图表 Validator 校验：
    #   时间 + 序列维度 + 数值
    # 导出给用户时则更适合宽表：
    #   时间 | 站点A | 站点B | 站点C
    # 这里根据 metadata.series_dimension 动态透视，不绑定具体站点数量或名称。
    frame = _reshape_artifact_for_export(frame, artifact)

    # 面向用户的表格使用字段 label；遇到重复 label 时追加字段名，
    # 防止 Excel 出现两个同名列导致后续读取歧义。
    used_labels: dict[str, int] = {}
    rename_map: dict[str, str] = {}
    for field_name in ordered:
        definition = artifact.field_schema.get(field_name)
        label = getattr(definition, "label", None) or field_name
        unit = getattr(definition, "unit", None)
        if unit and unit not in label:
            label = f"{label} ({unit})"
        count = used_labels.get(label, 0) + 1
        used_labels[label] = count
        rename_map[field_name] = label if count == 1 else f"{label}_{count}"
    return frame.rename(columns=rename_map)


def _reshape_artifact_for_export(frame: pd.DataFrame, artifact) -> pd.DataFrame:
    """将多序列长表转换为用户更易读的宽表。

    透视条件是：数据制品声明了序列维度、存在时间/日期轴、且只有一个
    数值字段。单序列或无法安全确定维度时保留原始长表，避免误改其它
    类型的数据制品。
    """
    if frame.empty:
        return frame

    schema = artifact.field_schema
    axis_field = next(
        (
            name
            for name, definition in schema.items()
            if name in frame.columns and definition.role in {"time", "date"}
        ),
        None,
    )
    series_dimension = artifact.metadata.get("series_dimension")
    if not isinstance(series_dimension, str) or series_dimension not in frame.columns:
        return frame

    measure_fields = [
        name
        for name, definition in schema.items()
        if name in frame.columns and definition.role == "measure"
    ]
    if axis_field is None or len(measure_fields) != 1:
        return frame

    measure_field = measure_fields[0]
    series_values = [
        value for value in frame[series_dimension].tolist()
        if value is not None and str(value).strip()
    ]
    ordered_series = list(dict.fromkeys(str(value) for value in series_values))
    if len(ordered_series) <= 1:
        return frame

    # sort=False 保留数据制品声明的序列顺序，避免 Excel 列顺序每次漂移。
    reshaped = (
        frame.assign(
            _axis_key=frame[axis_field].map(str),
            _series_key=frame[series_dimension].map(str),
        )
        .pivot_table(
            index=[axis_field, "_axis_key"],
            columns="_series_key",
            values=measure_field,
            aggfunc="first",
            sort=False,
        )
        .reset_index()
    )
    reshaped = reshaped.drop(columns=["_axis_key"])

    # pivot_table 可能产生 CategoricalIndex，统一转换为普通字符串列名。
    reshaped.columns = [str(column) for column in reshaped.columns]

    # 数据来源（actual/predicted）等内部枚举值，导出时使用数据制品声明的
    # 业务标签；站点名称通常已经是最终展示名，因此会自然保持不变。
    series_labels = artifact.metadata.get("series_labels") or {}
    rename_series = {
        raw_name: str(series_labels.get(raw_name, raw_name))
        for raw_name in ordered_series
        if raw_name in reshaped.columns
    }
    reshaped = reshaped.rename(columns=rename_series)
    return reshaped


def _chart_spec_to_dataframe(chart: dict) -> pd.DataFrame:
    """兼容旧测试的转换辅助函数；正式导出不再调用它。"""
    x_axis = chart.get("x_axis") if isinstance(chart, dict) else None
    series = chart.get("series") if isinstance(chart, dict) else None
    if not isinstance(x_axis, dict) or not isinstance(series, list):
        raise ToolException("图表快照缺少横轴或序列数据，无法导出")

    x_values = x_axis.get("data")
    if not isinstance(x_values, list) or not x_values:
        raise ToolException("图表快照横轴为空，无法导出")

    axis_name = str(x_axis.get("label") or "时间")
    result = {axis_name: x_values}
    used_names = {axis_name}
    for index, item in enumerate(series, start=1):
        if not isinstance(item, dict):
            continue
        values = item.get("data")
        if not isinstance(values, list) or len(values) != len(x_values):
            raise ToolException("图表快照序列长度与横轴不一致，无法导出")
        name = str(item.get("name") or f"序列{index}")
        unit = item.get("unit")
        if unit and str(unit) not in name:
            name = f"{name} ({unit})"
        base_name = name
        suffix = 2
        while name in used_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(name)
        result[name] = values

    return pd.DataFrame(result)


def _load_export_dataframe(artifact_id: str) -> tuple[pd.DataFrame, object]:
    """只从 DatasetArtifact 读取数据，禁止从图表快照反推表格。"""
    try:
        artifact = resolve_dataset_for_export(artifact_id)
    except DatasetArtifactError as exc:
        raise ToolException(f"导出数据制品不可用 [{exc.code}]: {exc.message}") from exc
    return _artifact_to_dataframe(artifact), artifact


def _resolve_artifact_filename(filename: str, file_format: str, artifact) -> str:
    """为任意数据制品生成稳定、可读且不暴露内部 ID 的文件名。"""
    if filename.strip():
        name = _sanitize_filename(filename.strip())
        base, ext = os.path.splitext(name)
        if ext.lower() not in {".xlsx", ".csv"}:
            name = f"{name}.{file_format}"
        elif ext.lower() != f".{file_format}":
            name = f"{base}.{file_format}"
        return name

    metadata = artifact.metadata or {}
    label = str(metadata.get("title") or metadata.get("station") or "数据制品")
    period_start = str(metadata.get("period_start") or "")
    period_end = str(metadata.get("period_end") or period_start)
    period = f"{period_start}_{period_end}" if period_start and period_end != period_start else period_start
    safe_label = _sanitize_filename(label) or "数据制品"
    suffix = f"_{period.replace('-', '')}" if period else ""
    return f"{safe_label}{suffix}.{file_format}"


def _load_tabular_sheets(full_path: str, filename: str) -> list[tuple[str, pd.DataFrame]]:
    """读取 CSV 或完整 Excel 工作簿，返回所有 Sheet，而不是默认第一个 Sheet。"""
    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext == ".csv":
            return [(os.path.basename(filename), pd.read_csv(full_path, encoding="utf-8-sig"))]
        if ext not in {".xlsx", ".xls"}:
            raise ToolException(f"不支持的文件格式: {ext}。支持 .xlsx、.xls、.csv")

        engine = "openpyxl" if ext == ".xlsx" else "xlrd"
        sheets: list[tuple[str, pd.DataFrame]] = []
        with pd.ExcelFile(full_path, engine=engine) as workbook:
            for sheet_name in workbook.sheet_names:
                sheets.append((sheet_name, workbook.parse(sheet_name=sheet_name)))
        return sheets
    except ToolException:
        raise
    except Exception as exc:
        raise ToolException(f"文件读取失败: {exc}") from exc


def _generate_workbook_summary(sheets: list[tuple[str, pd.DataFrame]], filename: str) -> str:
    """为多 Sheet 工作簿生成分 Sheet 摘要，避免只返回第一个 Sheet。"""
    lines = [
        f"文件: {filename}",
        f"Sheet 数量: {len(sheets)}",
    ]
    visible_sheets = sheets[:MAX_SUMMARY_SHEETS]
    for index, (sheet_name, frame) in enumerate(visible_sheets, start=1):
        lines.append("")
        lines.append(f"Sheet {index}: {sheet_name}")
        lines.append(_generate_table_summary(frame, f"{filename} / {sheet_name}"))
    if len(sheets) > MAX_SUMMARY_SHEETS:
        lines.append("")
        lines.append(
            f"其余 {len(sheets) - MAX_SUMMARY_SHEETS} 个 Sheet 未展开预览，"
            "如需处理请使用 import_power_data 或指定 Sheet。"
        )
    return "\n".join(lines)


def _frame_summary_dict(frame: pd.DataFrame) -> dict:
    """生成单个 Sheet 的结构化摘要，供 bytes API 复用。"""
    preview_records = []
    for _, row in frame.head(5).iterrows():
        record = {}
        for col in frame.columns:
            value = row[col]
            if pd.isna(value):
                record[str(col)] = None
            elif hasattr(value, "isoformat"):
                record[str(col)] = value.isoformat()
            else:
                record[str(col)] = value
        preview_records.append(record)

    stats = {}
    for col in frame.select_dtypes(include=["number"]).columns:
        stats[str(col)] = {
            "min": round(float(frame[col].min()), 2),
            "max": round(float(frame[col].max()), 2),
            "mean": round(float(frame[col].mean()), 2),
        }
    return {
        "rows": len(frame),
        "cols": len(frame.columns),
        "columns": [str(col) for col in frame.columns],
        "preview": preview_records,
        "stats": stats,
    }


# ============================================================
# LangChain Tools (@tool, 暴露给 LLM)
# ============================================================

@tool
def export_table(
    artifact_id: Annotated[str, "数据制品 ID。优先使用 get_power_dataset 返回的 artifact_id，可导出多站点、多序列和其他标准化表格"] = "",
    station_name: Annotated[str, "兼容旧查询的站点名称,如 '英杰'。使用 artifact_id 时不需要传"] = "",
    target_date: Annotated[str, "兼容旧查询的日期。使用 artifact_id 时不需要传"] = "",
    data_type: Annotated[str, "兼容旧查询的数据类型。使用 artifact_id 时不需要传"] = "",
    file_format: Annotated[str, "文件格式: 'xlsx' 或 'csv',默认 xlsx"] = "xlsx",
    filename: Annotated[str, "文件名(可选),不传则自动生成"] = "",
    end_date: Annotated[str, "结束日期(可选),支持 'YYYY-MM-DD'、'6月1日'、'6.1'、'后天' 等。传入时 target_date 作为起始日期,导出日期范围内的全部原始逐小时数据。仅对 data_type='actual' 有效"] = "",
) -> str:
    """导出标准化数据制品或兼容旧查询结果为 Excel/CSV。

    首选调用方式:
        先通过 get_power_dataset 获取 artifact_id，再传 artifact_id 导出。
        这种方式不绑定单站点或固定 data_type，支持多站点、多序列、
        实际/预测数据制品以及未来注册的其他标准化表格。

    兼容调用方式:
        没有 artifact_id 时，仍支持 station_name + target_date + data_type
        的旧单站点查询路径。

    日期范围导出:
        当传入 end_date 时,target_date 作为起始日期,导出范围内全部原始逐小时数据。
        仅对 data_type='actual' 有效,其他类型忽略 end_date。

    返回:
        写入成功后的 file_id、文件名和文件大小；不返回服务器真实路径或下载链接。
    """
    file_format = _validate_format(file_format)
    if not FILE_DIR:
        raise ToolException("FILE_DIR 未配置,请在 .env 中设置 FILE_DIR")
    os.makedirs(FILE_DIR, exist_ok=True)

    if artifact_id.strip():
        df, artifact = _load_export_dataframe(artifact_id.strip())
        if df.empty:
            raise ToolException("数据制品为空，无法导出表格")
        final_name = _resolve_artifact_filename(filename, file_format, artifact)
    else:
        if not station_name.strip() or not target_date.strip() or not data_type.strip():
            # 支持多轮对话中的“导出刚才的数据”，不再强制要求再次传查询参数。
            df, artifact = _load_export_dataframe("")
            if df.empty:
                raise ToolException("当前数据制品为空，无法导出表格")
            final_name = _resolve_artifact_filename(filename, file_format, artifact)
        else:
            # 旧查询路径保留，避免已有单站点导出调用立即失效。
            from backend.tools.power_query_tool import _resolve_station_id
            from backend.tools.date_parser_tool import parse_flexible_date
            station_id, info = _resolve_station_id(station_name)
            predict_date = parse_flexible_date(target_date)
            parsed_end_date = parse_flexible_date(end_date) if end_date.strip() else None
            df = _fetch_data(data_type, station_id, info, predict_date, station_name, parsed_end_date)
            if len(df) == 0:
                raise ToolException(
                    f"未获取到数据: {info['name']} {predict_date} {DATA_TYPE_LABELS.get(data_type, data_type)}"
                )
            date_for_name = f"{predict_date}_{parsed_end_date}" if parsed_end_date else predict_date
            final_name = _resolve_filename(filename, file_format, station_name, date_for_name, data_type)

    filepath = os.path.join(FILE_DIR, final_name)
    filepath = _get_unique_filepath(filepath)

    # 写入文件
    _write_dataframe(df, filepath, file_format)

    rows = len(df)
    cols = len(df.columns)
    # 表格已经落盘后登记为文件产物；Agent 和前端只使用 file_id 下载。
    artifact = register_current_generated_file(
        filepath,
        filename=final_name,
        source_tool="export_table",
    )
    if artifact and artifact.get("file_id"):
        return json.dumps(
            {
                "status": "ready",
                "result_type": "file",
                "message": f"已导出 {rows} 行 × {cols} 列数据，可以下载",
                "file": artifact,
            },
            ensure_ascii=False,
        )
    if artifact is not None:
        return json.dumps(
            {
                "status": "generated_unregistered",
                "result_type": "file",
                "message": "文件已生成，但下载记录暂时不可用",
                "file": None,
            },
            ensure_ascii=False,
        )
    # 离线直接调用工具时没有 Agent 请求上下文，保留原有兼容行为。
    return f"✅ 已导出 {rows} 行 × {cols} 列数据到文件: {filepath}"


@tool
def read_table(
    filename: Annotated[str, "要读取的历史表格文件名"] = "",
    attachment_id: Annotated[str, "当前对话附件ID"] = "",
) -> str:
    """读取 Excel/CSV 文件并返回数据摘要。

    支持场景:用户要求查看之前导出的表格文件或当前对话附件。
    返回行列数、列名、前 5 行预览、数值列统计信息。
    历史文件使用 filename，当前对话附件使用 attachment_id。
    支持 .xlsx、.xls、.csv 格式。

    返回:
        文件数据的文字摘要(不全量返回,避免内容过长)
    """
    if attachment_id:
        try:
            attachment, resolved_path = resolve_bound_attachment_path(attachment_id)
        except Exception as exc:
            raise ToolException(str(exc)) from exc
        filename = attachment["filename"]
        full_path = str(resolved_path)
    else:
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

    # 统一读取完整工作簿。pd.read_excel 默认 sheet_name=0，
    # 只会返回第一个 Sheet；这里显式遍历所有 Sheet，避免忽略多站点子表。
    sheets = _load_tabular_sheets(full_path, filename)
    return _generate_workbook_summary(sheets, filename)


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
    from backend.tools.cache_manager import (
        read_prediction_cache, read_archive_cache, read_forecast_cache,
        get_prediction_data_mode,
    )
    
    from backend.tools.power_query_tool import _query_actual_power, _query_actual_power_range

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
        df = read_prediction_cache(
            station_id, predict_date, get_prediction_data_mode(predict_date)
        )
        if df is None:
            from backend.tools.pv_predictor import predict_station_power
            lat = info["lat"]
            lon = info["lon"]
            history_date = (datetime.strptime(predict_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            summary, pred_df, weather_type = predict_station_power(
                station_name=station_name,
                lat=lat,
                lon=lon,
                station_id=station_id,
                predict_date=predict_date,
                history_date=history_date,
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
        pred_df = read_prediction_cache(
            station_id, predict_date, get_prediction_data_mode(predict_date)
        )
        if pred_df is None:
            from backend.tools.pv_predictor import predict_station_power
            lat = info["lat"]
            lon = info["lon"]
            history_date = (datetime.strptime(predict_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            summary, pred_df, weather_type = predict_station_power(
                station_name=station_name,
                lat=lat,
                lon=lon,
                station_id=station_id,
                predict_date=predict_date,
                history_date=history_date,
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
            from backend.tools.weather_fetcher_tool import _fetch_from_archive
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
            from backend.tools.weather_fetcher_tool import _fetch_from_forecast
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
    station_name: str = "",
    target_date: str = "",
    data_type: str = "",
    file_format: str = "xlsx",
    filename: str = "",
    end_date: str = "",
    artifact_id: str = "",
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
    file_format = _validate_format(file_format)

    if artifact_id.strip():
        df, artifact = _load_export_dataframe(artifact_id.strip())
        if df.empty:
            raise ToolException("数据制品为空，无法导出表格")
        final_name = _resolve_artifact_filename(filename, file_format, artifact)
    else:
        if not station_name.strip() or not target_date.strip() or not data_type.strip():
            df, artifact = _load_export_dataframe("")
            if df.empty:
                raise ToolException("当前数据制品为空，无法导出表格")
            final_name = _resolve_artifact_filename(filename, file_format, artifact)
        else:
            from backend.tools.power_query_tool import _resolve_station_id
            from backend.tools.date_parser_tool import parse_flexible_date
            station_id, info = _resolve_station_id(station_name)
            predict_date = parse_flexible_date(target_date)
            parsed_end_date = parse_flexible_date(end_date) if end_date.strip() else None
            df = _fetch_data(data_type, station_id, info, predict_date, station_name, parsed_end_date)
            if len(df) == 0:
                raise ToolException(
                    f"未获取到数据: {info['name']} {predict_date} {DATA_TYPE_LABELS.get(data_type, data_type)}"
                )
            date_for_name = f"{predict_date}_{parsed_end_date}" if parsed_end_date else predict_date
            final_name = _resolve_filename(filename, file_format, station_name, date_for_name, data_type)

    return {
        "filename": final_name,
        "bytes": _dataframe_to_bytes(df, file_format),
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
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_READ_EXTENSIONS:
        raise ToolException(f"不支持的文件格式: {ext}。支持 {', '.join(ALLOWED_READ_EXTENSIONS)}")

    try:
        if ext == ".csv":
            import io
            sheets = [(os.path.basename(filename), pd.read_csv(io.BytesIO(file_bytes), encoding="utf-8-sig"))]
        else:
            import io
            engine = "openpyxl" if ext == ".xlsx" else "xlrd"
            with pd.ExcelFile(io.BytesIO(file_bytes), engine=engine) as workbook:
                sheets = [
                    (sheet_name, workbook.parse(sheet_name=sheet_name))
                    for sheet_name in workbook.sheet_names
                ]
    except Exception as exc:
        raise ToolException(f"文件读取失败: {exc}") from exc

    sheet_summaries = []
    for sheet_name, frame in sheets:
        sheet_summaries.append({
            "sheet_name": sheet_name,
            **_frame_summary_dict(frame),
        })

    total_rows = sum(item["rows"] for item in sheet_summaries)
    max_cols = max((item["cols"] for item in sheet_summaries), default=0)
    first = sheet_summaries[0] if sheet_summaries else {
        "rows": 0,
        "cols": 0,
        "columns": [],
        "preview": [],
        "stats": {},
    }
    return {
        "filename": filename,
        "sheet_count": len(sheet_summaries),
        "sheets": sheet_summaries,
        "rows": total_rows,
        "cols": max_cols,
        # 保留单 Sheet API 的兼容字段；多 Sheet 时额外返回 sheets。
        "columns": first["columns"],
        "preview": first["preview"],
        "stats": first["stats"],
    }
