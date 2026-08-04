"""Normalized power datasets for charting.

The charting layer deliberately separates *where data comes from* from
*which chart should be rendered*.  Source loaders return a small, predictable
DataFrame; the dataset tool turns it into a permission-scoped artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


class DatasetSourceError(RuntimeError):
    """Raised when a registered data source cannot provide the requested data."""


@dataclass(frozen=True)
class PowerSourceDefinition:
    source_type: str
    label: str
    loader: Callable[[str, str], pd.DataFrame]
    range_loader: Callable[[str, str, str], pd.DataFrame]


def _load_actual(station_id: str, target_date: str) -> pd.DataFrame:
    from backend.app.services.power_data_service import load_actual_power

    return load_actual_power(station_id, target_date)


def _load_predicted(station_id: str, target_date: str) -> pd.DataFrame:
    from backend.app.services.power_data_service import load_predicted_power

    return load_predicted_power(station_id, target_date)


def _load_actual_range(station_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    from backend.app.services.power_data_service import load_actual_power_range

    return load_actual_power_range(station_id, start_date, end_date)


def _load_predicted_range(station_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    from backend.app.services.power_data_service import load_predicted_power_range

    return load_predicted_power_range(station_id, start_date, end_date)


POWER_SOURCE_REGISTRY: dict[str, PowerSourceDefinition] = {
    "actual": PowerSourceDefinition("actual", "实际发电量", _load_actual, _load_actual_range),
    "predicted": PowerSourceDefinition("predicted", "预测发电量", _load_predicted, _load_predicted_range),
}


def normalize_source_types(source_types: list[str] | None) -> list[str]:
    """Normalize and validate source types without coupling to chart choices."""

    values = source_types or ["actual"]
    normalized = [str(value).strip().lower() for value in values if str(value).strip()]
    if not normalized:
        normalized = ["actual"]
    unknown = sorted(set(normalized) - set(POWER_SOURCE_REGISTRY))
    if unknown:
        raise DatasetSourceError(
            f"不支持的数据来源: {', '.join(unknown)}。可用来源: {', '.join(POWER_SOURCE_REGISTRY)}"
        )
    # A request may mention a source twice, but an artifact must not duplicate it.
    return list(dict.fromkeys(normalized))


def load_power_source(source_type: str, station_id: str, target_date: str) -> pd.DataFrame:
    definition = POWER_SOURCE_REGISTRY[source_type]
    frame = definition.loader(station_id, target_date)
    if frame is None or frame.empty:
        raise DatasetSourceError(
            f"{definition.label}数据不存在: station_id={station_id}, date={target_date}"
        )
    frame = frame.copy()
    frame["value_kwh"] = pd.to_numeric(frame["value_kwh"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "value_kwh"])
    if frame.empty:
        raise DatasetSourceError(f"{definition.label}数据为空或包含无效数值: {target_date}")
    return frame.sort_values("timestamp").reset_index(drop=True)


def load_power_source_range(source_type: str, station_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    definition = POWER_SOURCE_REGISTRY[source_type]
    frame = definition.range_loader(station_id, start_date, end_date)
    if frame is None or frame.empty:
        raise DatasetSourceError(
            f"{definition.label}数据不存在: station_id={station_id}, range={start_date}~{end_date}"
        )
    frame = frame.copy()
    frame["value_kwh"] = pd.to_numeric(frame["value_kwh"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "value_kwh"])
    if frame.empty:
        raise DatasetSourceError(f"{definition.label}数据为空或包含无效数值: {start_date}~{end_date}")
    return frame.sort_values("timestamp").reset_index(drop=True)
