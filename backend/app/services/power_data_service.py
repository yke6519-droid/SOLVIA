"""统一读取实际与预测发电数据的服务。"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from backend.app.database import get_engine
from backend.tools.cache_manager import get_prediction_data_mode, read_prediction_cache


def query_actual_power(station_id: str, target_date: str) -> pd.DataFrame:
    """读取单日实际发电原始记录，保留旧字段供业务工具兼容。"""

    query = text(
        """
        SELECT record_time, power_kwh
        FROM power_generation
        WHERE station_id = :sid
          AND DATE(record_time) = :dt
        ORDER BY record_time
        """
    )
    with get_engine().connect() as conn:
        return pd.read_sql(
            query,
            conn,
            params={"sid": station_id, "dt": target_date},
        )


def query_actual_power_range(
    station_id: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """读取日期范围内的实际发电原始记录，返回逐条记录。"""

    query = text(
        """
        SELECT record_time, power_kwh
        FROM power_generation
        WHERE station_id = :sid
          AND DATE(record_time) BETWEEN :start AND :end
        ORDER BY record_time
        """
    )
    with get_engine().connect() as conn:
        return pd.read_sql(
            query,
            conn,
            params={"sid": station_id, "start": start_date, "end": end_date},
        )


def query_predicted_power(station_id: str, target_date: str) -> pd.DataFrame | None:
    """读取单日预测缓存，保留缓存未命中的 None 语义。"""

    frame = read_prediction_cache(
        station_id,
        target_date,
        get_prediction_data_mode(target_date),
    )
    return frame


def query_predicted_power_range(
    station_id: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """按自然日读取预测缓存并合并成一个原始数据表。"""

    frames = []
    for day in pd.date_range(start_date, end_date, freq="D"):
        frame = query_predicted_power(station_id, day.strftime("%Y-%m-%d"))
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["time", "fusion"])
    return pd.concat(frames, ignore_index=True)


def _normalize_power_frame(
    frame: pd.DataFrame | None,
    *,
    timestamp_column: str,
    value_column: str,
) -> pd.DataFrame:
    """把不同数据源的字段统一为图表层使用的标准字段。"""

    if frame is None or frame.empty:
        return pd.DataFrame(columns=["timestamp", "value_kwh"])
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame[timestamp_column], errors="coerce"),
            "value_kwh": pd.to_numeric(frame[value_column], errors="coerce"),
        }
    )


def load_actual_power(station_id: str, target_date: str) -> pd.DataFrame:
    """读取并标准化单日实际发电数据。"""

    return _normalize_power_frame(
        query_actual_power(station_id, target_date),
        timestamp_column="record_time",
        value_column="power_kwh",
    )


def load_actual_power_range(
    station_id: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """读取并标准化日期范围内的实际发电数据。"""

    return _normalize_power_frame(
        query_actual_power_range(station_id, start_date, end_date),
        timestamp_column="record_time",
        value_column="power_kwh",
    )


def load_predicted_power(station_id: str, target_date: str) -> pd.DataFrame:
    """读取并标准化单日预测发电数据。"""

    return _normalize_power_frame(
        query_predicted_power(station_id, target_date),
        timestamp_column="time",
        value_column="fusion",
    )


def load_predicted_power_range(
    station_id: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """读取并标准化日期范围内的预测发电数据。"""

    return _normalize_power_frame(
        query_predicted_power_range(station_id, start_date, end_date),
        timestamp_column="time",
        value_column="fusion",
    )
