"""
import_tool.py - 数据导入入库工具 (LangChain Tool)
====================================================
读取 Excel 文件，自动解析站点信息和发电量数据，清洗后入库 MySQL。

工具列表(@tool, 暴露给 LLM):
  1. import_power_data  导入发电量数据到数据库(由 Runtime 负责预览确认)

公共函数(供前端 API 直接调用):
  - parse_excel_preview(file_path, file_bytes, filename, skip_clean)
      解析+清洗，返回 JSON 可序列化的预览(不入库)
  - execute_import(file_path, file_bytes, filename, skip_clean, expected_preview_hash)
      读文件+清洗+入库，返回统一结果(不询问用户)

设计说明:
  - 合并 import_station_data.py 和 import_multi_station.py 的逻辑为一个工具
  - 自动检测单 Sheet / 多 Sheet 格式
  - 数据清洗: 去空/去重/零值天过滤(≥80%为0的整天删除)
  - 入库前调用 ask_user 让用户确认, 用户拒绝则不写入任何数据
  - 幂等: INSERT IGNORE + 联合唯一索引, 重复数据自动跳过
  - DB 配置使用 .env 的 MYSQL_URL(不硬编码)
  - 文件从 .env 的 FILE_DIR 读取
  - 路径安全: 防止 ../ 路径穿越
  - 前端对接: 支持传 file_bytes(内存字节流)，无需落盘即可解析
"""
import os
import re
import json
import hashlib
import io
import pandas as pd
from datetime import datetime
from typing import Annotated, Optional, Tuple, List, Union
from sqlalchemy import text
from langchain_core.tools import tool, ToolException
from dotenv import load_dotenv
from backend.app.database import get_engine
from backend.app.errors import ErrorCode, ToolError
from backend.app.runtime.context import get_runtime_context
from backend.app.runtime.enums import InteractionIntent, InteractionState
from backend.app.runtime.interactions import make_confirmation_key
from backend.app.runtime.models import ConfirmationRequest
from backend.app.services.attachment_context import get_attachment_context
from backend.app.services.attachment_service import resolve_attachment_path

load_dotenv()

FILE_DIR = os.getenv("FILE_DIR", "")
MYSQL_URL = os.environ.get("MYSQL_URL")

# 允许的文件后缀
ALLOWED_EXTENSIONS = (".xlsx", ".xls")


# ============================================================
# 内部辅助函数
# ============================================================

def _generate_station_code(name: str, capacity_kw: Optional[float]) -> str:
    """生成站点编号: 名称 MD5 哈希前8位 + 容量标记。"""
    hash_part = hashlib.md5(name.encode('utf-8')).hexdigest()[:8].upper()
    cap_part = f"{int(capacity_kw)}KW" if capacity_kw else ""
    return f"{hash_part}-{cap_part}" if cap_part else hash_part


def _parse_station_info(title: str) -> dict:
    """
    从标题行解析站点信息。

    支持两种格式:
      1. "宁波海曙英杰250KW光伏"
         → province=浙江, city=宁波市, name=宁波海曙英杰250KW光伏, capacity=250
      2. "浙江省-衢州市-哲丰新材料清水站新增"
         → province=浙江省, city=衢州市, name=哲丰新材料清水站新增, capacity=None
    """
    title = str(title).strip()

    # 提取装机容量: 250KW / 1.5MW 等
    kw_match = re.search(r'(\d+(?:\.\d+)?)\s*(KW|kw|Kw|kWp|KWp)', title, re.IGNORECASE)
    mw_match = re.search(r'(\d+(?:\.\d+)?)\s*(MW|mw|Mw|MWp)', title, re.IGNORECASE)

    capacity_kw = None
    if kw_match:
        capacity_kw = float(kw_match.group(1))
    elif mw_match:
        capacity_kw = float(mw_match.group(1)) * 1000  # MW → kW

    # 解析省市信息
    province = None
    city = None
    name = title

    if '-' in title:
        parts = title.split('-')
        if len(parts) >= 3:
            province = parts[0].strip()
            city = parts[1].strip()
            name = '-'.join(parts[2:]).strip()
        elif len(parts) == 2:
            province = parts[0].strip()
            name = parts[1].strip()
    else:
        city_keywords = {
            '宁波': ('浙江省', '宁波市'), '杭州': ('浙江省', '杭州市'),
            '衢州': ('浙江省', '衢州市'), '温州': ('浙江省', '温州市'),
            '绍兴': ('浙江省', '绍兴市'), '芜湖': ('安徽省', '芜湖市'),
        }
        for keyword, (prov, city_name) in city_keywords.items():
            if keyword in title:
                province = prov
                city = city_name
                break

    station_code = _generate_station_code(name, capacity_kw)

    return {
        'station_code': station_code,
        'name': name,
        'capacity_kw': capacity_kw,
        'location': title,
        'province': province,
        'city': city,
    }


def _read_excel_file(
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    filename: str = "",
) -> List[Tuple[str, pd.DataFrame]]:
    """
    读取 Excel 文件，返回 [(站点标题, 原始DataFrame), ...]。

    参数:
      file_path   — 文件磁盘路径(二选一)
      file_bytes  — 文件内存字节流(二选一，前端上传场景)
      filename    — 文件名(用于判断扩展名，file_bytes 模式下必传)

    自动检测单 Sheet / 多 Sheet，每个 Sheet 一个站点。
    每个 Sheet 格式:
      - 第0行第0列 = 站点标题
      - 第1行 = 表头
      - 第2行起 = 数据
      - 2列: 日期 + 光伏发电(kWh) → 取 col0 + col1
      - 3列+: 日期 + 市电 + 光伏发电(kWh) + ... → 取 col0 + col2
    """
    # 确定数据源和引擎
    if file_bytes is not None:
        name_for_ext = filename or "upload.xlsx"
        ext = os.path.splitext(name_for_ext)[1].lower()
        source = io.BytesIO(file_bytes)
    elif file_path is not None:
        ext = os.path.splitext(file_path)[1].lower()
        source = file_path
    else:
        raise ToolException("必须提供 file_path 或 file_bytes")

    if ext == '.xlsx':
        engine = 'openpyxl'
    elif ext == '.xls':
        engine = 'xlrd'
    else:
        raise ToolException(f"不支持的文件格式: {ext}。支持 .xlsx 和 .xls")

    xls = pd.ExcelFile(source, engine=engine)

    results = []
    for sheet_name in xls.sheet_names:
        # 旧实现对每个 Sheet 调用两次 pd.read_excel：一次取标题、一次取数据。
        # 多站点文件会重复解析同一个 Sheet，改为通过 ExcelFile.parse 一次读完，
        # 再拆出标题行、表头行和数据区，减少明显的重复 IO/解析开销。
        raw = xls.parse(sheet_name=sheet_name, header=None)
        if raw.empty or raw.shape[0] < 2 or raw.shape[1] == 0:
            raise ToolException(f"Sheet '{sheet_name}' 内容为空或缺少标题/表头")

        title = str(raw.iloc[0, 0]).strip()
        df = raw.iloc[2:].copy().dropna(axis=1, how='all').reset_index(drop=True)

        num_cols = len(df.columns)
        if num_cols == 2:
            df = df.iloc[:, :2]
            df.columns = ['record_time', 'power_kwh']
        elif num_cols >= 3:
            # 光伏发电在第3列(index=2)
            df = df.iloc[:, [0, 2]]
            df.columns = ['record_time', 'power_kwh']
        else:
            raise ToolException(f"Sheet '{sheet_name}' 列数异常: {num_cols} 列，需2列或以上")

        results.append((title, df))

    return results


def _read_source_bytes(
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
) -> bytes:
    """读取用于生成预览指纹的原始文件内容。"""

    if file_bytes is not None:
        return file_bytes
    if file_path is not None:
        with open(file_path, "rb") as source:
            return source.read()
    raise ToolException("必须提供 file_path 或 file_bytes")


def make_preview_hash(
    *,
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    filename: str = "",
    skip_clean: bool = False,
) -> str:
    """为预览生成稳定指纹，绑定原始文件、文件名和清洗选项。"""

    source_bytes = _read_source_bytes(file_path=file_path, file_bytes=file_bytes)
    display_name = filename or (os.path.basename(file_path) if file_path else "unknown")
    metadata = json.dumps(
        {
            "filename": display_name,
            "skip_clean": bool(skip_clean),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(source_bytes)
    digest.update(b"\0")
    digest.update(metadata)
    return digest.hexdigest()


def _clean_data(df: pd.DataFrame, skip_zero_filter: bool = False) -> Tuple[pd.DataFrame, dict]:
    """
    数据清洗，返回 (清洗后DataFrame, 清洗统计)。

    清洗步骤:
      1. 去空行
      2. 解析日期时间(支持多种格式)
      3. 发电量转数值，异常值置0，不能为负
      4. 去重(同一时间点保留最后一条)
      5. 零值天过滤(可跳过): 整天24h中≥80%为0 → 删除该天全部数据
    """
    stats = {
        'original': len(df),
        'after': 0,
        'removed_days': 0,
        'removed_rows': 0,
    }

    # 1. 去空行
    df = df.dropna(subset=['record_time']).reset_index(drop=True)

    # 2. 解析日期时间
    def parse_datetime(val):
        val = str(val).strip()
        for fmt in ['%Y-%m-%d %H', '%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S']:
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
        try:
            return pd.to_datetime(val)
        except Exception:
            return None

    df['record_time'] = df['record_time'].apply(parse_datetime)
    df = df.dropna(subset=['record_time']).reset_index(drop=True)

    # 3. 发电量转数值
    df['power_kwh'] = pd.to_numeric(df['power_kwh'], errors='coerce').fillna(0)
    df['power_kwh'] = df['power_kwh'].clip(lower=0)

    # 4. 去重
    df = df.drop_duplicates(subset=['record_time'], keep='last').reset_index(drop=True)

    # 5. 零值天过滤
    if not skip_zero_filter and len(df) > 0:
        df['date'] = df['record_time'].dt.date
        valid_days = []
        for date, group in df.groupby('date'):
            total_hours = len(group)
            zero_hours = (group['power_kwh'] == 0).sum()
            zero_ratio = zero_hours / total_hours if total_hours > 0 else 1.0
            if zero_ratio >= 0.8:
                stats['removed_days'] += 1
                stats['removed_rows'] += total_hours
            else:
                valid_days.append(date)
        df = df[df['date'].isin(valid_days)].copy()
        df = df.drop(columns=['date']).reset_index(drop=True)

    stats['after'] = len(df)
    return df, stats


def _upsert_station(conn, station_info: dict) -> int:
    """
    插入或更新站点信息，返回 station_id。
    按 station_code 判断是否已存在(幂等)。
    """
    result = conn.execute(
        text("SELECT id FROM solar_station WHERE station_code = :code"),
        {"code": station_info['station_code']}
    ).fetchone()

    if result:
        station_id = result[0]
        conn.execute(text("""
            UPDATE solar_station
            SET name = :name,
                capacity_kw = COALESCE(capacity_kw, :capacity),
                location = COALESCE(location, :location),
                province = COALESCE(province, :province),
                city = COALESCE(city, :city),
                updated_at = NOW()
            WHERE id = :id
        """), {
            "name": station_info['name'],
            "capacity": station_info['capacity_kw'],
            "location": station_info['location'],
            "province": station_info['province'],
            "city": station_info['city'],
            "id": station_id,
        })
    else:
        insert_result = conn.execute(text("""
            INSERT INTO solar_station (station_code, name, capacity_kw, location, province, city)
            VALUES (:code, :name, :capacity, :location, :province, :city)
        """), {
            "code": station_info['station_code'],
            "name": station_info['name'],
            "capacity": station_info['capacity_kw'],
            "location": station_info['location'],
            "province": station_info['province'],
            "city": station_info['city'],
        })
        station_id = insert_result.lastrowid

    return station_id


def _batch_insert_generation(conn, station_id: int, df: pd.DataFrame) -> dict:
    """
    批量插入发电量数据。
    利用联合唯一索引做 INSERT IGNORE，自动跳过重复数据(幂等)。
    """
    if df.empty:
        return {"total": 0, "inserted": 0, "skipped": 0}

    records = []
    for _, row in df.iterrows():
        records.append({
            "station_id": station_id,
            "record_time": row['record_time'].strftime('%Y-%m-%d %H:%M:%S'),
            "power_kwh": float(row['power_kwh']),
        })

    total = len(records)
    inserted = 0

    for batch_start in range(0, total, 1000):
        batch = records[batch_start:batch_start + 1000]
        result = conn.execute(text("""
            INSERT IGNORE INTO power_generation (station_id, record_time, power_kwh)
            VALUES (:station_id, :record_time, :power_kwh)
        """), batch)
        inserted += result.rowcount

    return {"total": total, "inserted": inserted, "skipped": total - inserted}


def _build_preview(filename: str, items: list) -> str:
    """生成入库预览摘要，供 ask_user 展示给用户确认。"""
    lines = [f"📊 数据导入预览", f"文件: {filename}", f"检测到 {len(items)} 个站点:", ""]

    for i, item in enumerate(items):
        info = item['station_info']
        cap_str = f"{info['capacity_kw']:.0f} kW" if info['capacity_kw'] else "未知"
        clean = item['clean_stats']
        time_min = item['time_range'][0] if item['time_range'] else "N/A"
        time_max = item['time_range'][1] if item['time_range'] else "N/A"

        lines.append(f"① {info['name']}".replace("①", f"{i+1}.") if i > 0 else f"1. {info['name']}")
        lines.append(f"   装机容量: {cap_str}")
        if clean['removed_days'] > 0:
            lines.append(f"   原始 {clean['original']} 行 → 清洗后 {clean['after']} 行 (删除 {clean['removed_days']} 天零值数据)")
        else:
            lines.append(f"   原始 {clean['original']} 行 → 清洗后 {clean['after']} 行")
        lines.append(f"   时间范围: {time_min} ~ {time_max}")
        lines.append("")

    lines.append("⚠️ 入库操作将写入 MySQL 数据库")
    lines.append('确认导入？(回复"是"继续)')

    return "\n".join(lines)


def _build_import_confirmation_question(preview: dict) -> str:
    """把结构化预览转换成确认问题；用户回复由 Runtime 解释器处理。"""

    lines = [
        "📊 数据导入预览",
        f"文件: {preview['filename']}",
        f"检测到 {preview['station_count']} 个站点:",
        "",
    ]
    for index, station in enumerate(preview["stations"], start=1):
        capacity = (
            f"{station['capacity_kw']:.0f} kW"
            if station["capacity_kw"]
            else "未知"
        )
        lines.append(f"{index}. {station['name']}")
        lines.append(f"   装机容量: {capacity}")
        if station["removed_days"] > 0:
            lines.append(
                f"   原始 {station['original_rows']} 行 → 清洗后 "
                f"{station['cleaned_rows']} 行 "
                f"(删除 {station['removed_days']} 天零值数据)"
            )
        else:
            lines.append(
                f"   原始 {station['original_rows']} 行 → "
                f"清洗后 {station['cleaned_rows']} 行"
            )
        if station["time_range"]:
            lines.append(
                f"   时间范围: {station['time_range'][0]} ~ "
                f"{station['time_range'][1]}"
            )
        lines.append("")
    lines.extend(
        [
            "⚠️ 入库操作将写入 MySQL 数据库。",
            "请确认是否导入这份预览对应的数据。你可以回复“可以导入”、"
            "“先不要导入”或其他自然语言。",
        ]
    )
    return "\n".join(lines)


def _resolve_import_source(filename: str, attachment_id: str) -> tuple[str, str]:
    """统一解析导入文件来源，确保预览和执行使用同一套权限边界。"""

    if attachment_id:
        # Agent 只能提交 attachment_id；真实路径由服务端按用户/会话权限解析。
        attachment_context = get_attachment_context()
        if attachment_context is None:
            raise ToolException("当前没有可用的附件执行上下文")
        allowed_ids = {
            item.get("attachment_id") for item in attachment_context.attachments
        }
        if attachment_id not in allowed_ids:
            raise ToolException("附件不属于当前任务或当前会话")
        try:
            attachment, resolved_path = resolve_attachment_path(
                attachment_id,
                user_id=attachment_context.user_id,
                session_id=attachment_context.session_id,
            )
        except Exception as exc:
            raise ToolException(str(exc)) from exc
        return str(resolved_path), attachment["filename"]

    if not FILE_DIR:
        raise ToolException("FILE_DIR 未配置,请在 .env 中设置 FILE_DIR")

    # 路径安全:防止 ../ 路径穿越。
    full_path = os.path.normpath(os.path.join(FILE_DIR, filename))
    if not full_path.startswith(os.path.normpath(FILE_DIR)):
        raise ToolException("文件名包含非法路径")
    if not os.path.exists(full_path):
        raise ToolException(f"文件不存在: {filename}")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ToolException(
            f"不支持的文件格式: {ext}。支持 {', '.join(ALLOWED_EXTENSIONS)}"
        )
    return full_path, filename


def build_import_confirmation_request(
    _context: object,
    invocation: object,
    _spec: object,
) -> ConfirmationRequest:
    """R4-C 预览阶段：只解析文件，不写数据库，生成 Runtime 确认请求。"""

    arguments = (
        invocation.arguments if isinstance(invocation.arguments, dict) else {}
    )
    filename = str(arguments.get("filename") or "")
    attachment_id = str(arguments.get("attachment_id") or "")
    skip_clean = bool(arguments.get("skip_clean", False))
    full_path, resolved_filename = _resolve_import_source(filename, attachment_id)
    preview = parse_excel_preview(
        file_path=full_path,
        filename=resolved_filename,
        skip_clean=skip_clean,
    )
    confirmation_key = make_confirmation_key(
        "import_power_data",
        {"preview_hash": preview["preview_hash"]},
    )
    return ConfirmationRequest(
        confirmation_key=confirmation_key,
        question=_build_import_confirmation_question(preview),
        allowed_intents=[InteractionIntent.CONFIRM, InteractionIntent.CANCEL],
    )


# ============================================================
# 公共函数(供前端 API 直接调用)
# ============================================================

def parse_excel_preview(
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    filename: str = "",
    skip_clean: bool = False,
) -> dict:
    """
    解析 Excel 文件 + 数据清洗，返回 JSON 可序列化的预览(不入库)。

    前端对接流程:
      1. 用户上传文件 → 后端拿到 file_bytes
      2. 调本函数 → 返回预览 dict
      3. 前端展示预览 → 用户确认
      4. 用户确认 → 调 execute_import 入库

    参数:
      file_path   — 文件磁盘路径(与 file_bytes 二选一)
      file_bytes  — 文件内存字节流(前端上传场景)
      filename    — 文件名(file_bytes 模式下必传，用于判断扩展名)
      skip_clean  — 是否跳过零值天清洗

    返回:
      {
        "filename": "xxx.xlsx",
        "preview_hash": "sha256...",
        "station_count": 2,
        "stations": [
          {
            "name": "宁波海曙英杰250KW光伏",
            "capacity_kw": 250.0,
            "province": "浙江省",
            "city": "宁波市",
            "station_code": "0F1F9C66-250KW",
            "original_rows": 720,
            "cleaned_rows": 648,
            "removed_days": 3,
            "time_range": ["2026-01-01 00:00", "2026-01-31 23:00"]
          },
          ...
        ]
      }
    """
    sheets_data = _read_excel_file(file_path, file_bytes, filename)
    if not sheets_data:
        raise ToolException("文件中未检测到任何 Sheet")

    display_name = filename or (os.path.basename(file_path) if file_path else "unknown")
    preview_hash = make_preview_hash(
        file_path=file_path,
        file_bytes=file_bytes,
        filename=display_name,
        skip_clean=skip_clean,
    )

    stations = []
    for title, df_raw in sheets_data:
        station_info = _parse_station_info(title)
        df_clean, clean_stats = _clean_data(df_raw, skip_zero_filter=skip_clean)

        time_range = None
        if len(df_clean) > 0:
            time_range = [
                df_clean['record_time'].min().strftime('%Y-%m-%d %H:%M'),
                df_clean['record_time'].max().strftime('%Y-%m-%d %H:%M'),
            ]

        stations.append({
            "name": station_info['name'],
            "capacity_kw": station_info['capacity_kw'],
            "province": station_info['province'],
            "city": station_info['city'],
            "station_code": station_info['station_code'],
            "original_rows": clean_stats['original'],
            "cleaned_rows": clean_stats['after'],
            "removed_days": clean_stats['removed_days'],
            "time_range": time_range,
        })

    return {
        "filename": display_name,
        "preview_hash": preview_hash,
        "station_count": len(stations),
        "stations": stations,
    }


def execute_import(
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    filename: str = "",
    skip_clean: bool = False,
    expected_preview_hash: str | None = None,
) -> dict:
    """
    读文件 + 清洗 + 入库，返回结果(不询问用户)。

    前端对接流程:
      用户确认后调本函数 → 返回入库结果 dict

    参数:
      同 parse_excel_preview
      expected_preview_hash — 用户确认时拿到的预览指纹；缺失或不一致时拒绝入库

    返回统一 Runtime 工具结果，入库明细位于 data 字段。
    """
    if not MYSQL_URL:
        raise ToolException("MYSQL_URL 未配置,请在 .env 中设置 MYSQL_URL")

    if not expected_preview_hash:
        raise ToolError(
            ErrorCode.IMPORT_PREVIEW_REQUIRED,
            "执行导入前必须先完成预览并确认，缺少 preview_hash。",
            details={"action": "preview_then_confirm"},
        )

    # 执行前重新计算指纹，防止用户确认后文件被替换或清洗选项发生变化。
    current_preview = parse_excel_preview(
        file_path=file_path,
        file_bytes=file_bytes,
        filename=filename,
        skip_clean=skip_clean,
    )
    if current_preview["preview_hash"] != expected_preview_hash:
        raise ToolError(
            ErrorCode.IMPORT_PREVIEW_MISMATCH,
            "待导入文件或清洗选项已变化，请重新预览并确认。",
            details={
                "expected_preview_hash": expected_preview_hash,
                "current_preview_hash": current_preview["preview_hash"],
            },
        )

    sheets_data = _read_excel_file(file_path, file_bytes, filename)
    if not sheets_data:
        raise ToolException("文件中未检测到任何 Sheet")

    display_name = filename or (os.path.basename(file_path) if file_path else "unknown")

    engine = get_engine()
    stations_result = []
    total_inserted = 0
    total_skipped = 0

    # 一个文件就是一个事务：任意 Sheet 失败时，站点和发电量全部回滚。
    with engine.begin() as conn:
        for title, df_raw in sheets_data:
            station_info = _parse_station_info(title)
            df_clean, _ = _clean_data(df_raw, skip_zero_filter=skip_clean)

            if len(df_clean) == 0:
                stations_result.append({
                    "name": station_info['name'],
                    "station_id": None,
                    "inserted": 0,
                    "skipped": 0,
                    "message": "清洗后无数据,跳过",
                })
                continue

            station_id = _upsert_station(conn, station_info)
            stats = _batch_insert_generation(conn, station_id, df_clean)

            stations_result.append({
                "name": station_info['name'],
                "station_id": station_id,
                "inserted": stats['inserted'],
                "skipped": stats['skipped'],
            })
            total_inserted += stats['inserted']
            total_skipped += stats['skipped']

    has_data = any(
        item.get("inserted", 0) + item.get("skipped", 0) > 0
        for item in stations_result
    )
    status = "success" if has_data else "no_data"
    code = None if has_data else ErrorCode.IMPORT_NO_DATA.value
    message = "发电量数据导入完成" if has_data else "文件清洗后没有可入库的数据"
    suggested_actions = [] if has_data else ["检查 Excel 的日期列和发电量列是否正确"]
    return {
        "status": status,
        "code": code,
        "message": message,
        "data": {
            "result_type": "import",
            "filename": display_name,
            "station_count": len(stations_result),
            "total_inserted": total_inserted,
            "total_skipped": total_skipped,
            "stations": stations_result,
        },
        "retryable": False,
        "suggested_actions": suggested_actions,
        "artifact_ids": [],
    }


# ============================================================
# LangChain Tool (@tool, 暴露给 LLM)
# ============================================================

@tool
def import_power_data(
    filename: Annotated[str, "FILE_DIR目录下的旧文件名;对话附件优先传 attachment_id"] = "",
    attachment_id: Annotated[str, "对话附件ID,由附件上传接口返回"] = "",
    skip_clean: Annotated[bool, "是否跳过零值天清洗(默认False,执行清洗)"] = False,
) -> str:
    """导入发电量Excel数据到数据库。

    支持场景:用户要求导入、入库、上传发电量数据时调用。
    自动检测单Sheet/多Sheet格式，解析站点信息和发电量数据。
    数据清洗后，由 Runtime ConfirmationHook 展示预览并等待用户确认。
    支持幂等导入:重复数据自动跳过。

    返回:
        导入结果摘要(站点数、新增条数、跳过条数)
    """
    full_path, filename = _resolve_import_source(filename, attachment_id)

    # 1. 解析预览；真正的用户确认由 Runtime ConfirmationHook 完成。
    preview = parse_excel_preview(file_path=full_path, filename=filename, skip_clean=skip_clean)

    # 2. 只有当前 Runtime 运行中存在同一预览的已确认交互，才允许执行。
    context = get_runtime_context(required=False)
    confirmation_key = make_confirmation_key(
        "import_power_data",
        {"preview_hash": preview["preview_hash"]},
    )
    confirmed = bool(
        context
        and any(
            item.confirmation_key == confirmation_key
            and item.status == InteractionState.CONFIRMED
            for item in context.interaction_history
        )
    )
    if not confirmed:
        raise ToolError(
            ErrorCode.IMPORT_PREVIEW_REQUIRED,
            "导入必须先经过 Runtime 预览确认。",
            details={"preview_hash": preview["preview_hash"]},
        )

    # 3. 执行入库(复用公共函数)
    result = execute_import(
        file_path=full_path,
        filename=filename,
        skip_clean=skip_clean,
        expected_preview_hash=preview["preview_hash"],
    )

    # 4. 与文件、表格和图表工具一样，返回统一 JSON 结果协议。
    return json.dumps(result, ensure_ascii=False)
