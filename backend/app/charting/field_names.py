"""SOLVIA 图表与数据制品的统一字段命名。

数据库和预测模型各自有历史字段名，例如 ``record_time``、``power_kwh``、
``time``、``fusion``。这些字段在数据源边界保留，进入 DatasetArtifact、
ChartPlan 和图表构建链路后统一使用本模块定义的标准字段。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd


# 统一后的应用层字段。数据库列名和模型内部列名不在这里直接改名。
TIMESTAMP_FIELD = "timestamp"
VALUE_KWH_FIELD = "value_kwh"

# 这些别名只表示同一种“数据点时间”，进入数据制品时统一为 timestamp。
_DATASET_FIELD_ALIASES = {
    "time": TIMESTAMP_FIELD,
    "datetime": TIMESTAMP_FIELD,
    "record_time": TIMESTAMP_FIELD,
    "power_kwh": VALUE_KWH_FIELD,
}

# ChartPlan 只面向当前光伏图表链路，因此兼容预测模型和旧图表路径的别名。
_CHART_FIELD_ALIASES = {
    **_DATASET_FIELD_ALIASES,
    "fusion": VALUE_KWH_FIELD,
    "value": VALUE_KWH_FIELD,
}


class FieldNameCollisionError(ValueError):
    """同义字段同时存在，无法安全合并时抛出。"""


def _normalized_name(name: Any) -> str:
    return str(name).strip()


def canonical_dataset_field_name(name: Any) -> str:
    """返回数据制品使用的标准字段名。"""

    normalized = _normalized_name(name)
    return _DATASET_FIELD_ALIASES.get(normalized.lower(), normalized)


def canonical_chart_field_name(name: Any) -> str:
    """返回 ChartPlan 使用的标准字段名。"""

    normalized = _normalized_name(name)
    return _CHART_FIELD_ALIASES.get(normalized.lower(), normalized)


def _build_rename_map(columns: list[Any], canonicalizer) -> dict[Any, str]:
    """构建重命名表，并拒绝标准字段与别名同时出现。"""

    rename_map: dict[Any, str] = {}
    original_names = {_normalized_name(column) for column in columns}
    targets: dict[str, Any] = {}
    for column in columns:
        target = canonicalizer(column)
        if target in targets and targets[target] != column:
            raise FieldNameCollisionError(
                f"字段 {targets[target]!s} 与 {column!s} 都表示 {target}，无法安全合并"
            )
        targets[target] = column
        if target != column:
            rename_map[column] = target

    # 处理非别名字段恰好已经使用标准名的情况，避免重复检查遗漏。
    for target in targets:
        if target in original_names and targets[target] != target:
            raise FieldNameCollisionError(
                f"字段 {target} 与其别名同时存在，无法安全合并"
            )
    return rename_map


def normalize_dataset_frame_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """将进入 DatasetArtifact 的时间和电量列统一为标准名。"""

    rename_map = _build_rename_map(list(frame.columns), canonical_dataset_field_name)
    return frame.rename(columns=rename_map).copy()


def normalize_field_schema(field_schema: Mapping[str, Any]) -> dict[str, Any]:
    """同步标准化 DatasetArtifact 的字段描述，保持 schema 与 rows 一致。"""

    rename_map = _build_rename_map(list(field_schema.keys()), canonical_dataset_field_name)
    return {
        rename_map.get(name, name): definition
        for name, definition in field_schema.items()
    }


def normalize_dataset_artifact(artifact: Any) -> Any:
    """兼容读取历史制品，并把 schema 与 rows 同步转换为标准字段。"""

    normalized_rows = []
    for row in artifact.rows:
        rename_map = _build_rename_map(list(row.keys()), canonical_dataset_field_name)
        normalized_rows.append({
            rename_map.get(name, name): value
            for name, value in row.items()
        })
    return artifact.model_copy(update={
        "field_schema": normalize_field_schema(artifact.field_schema),
        "rows": normalized_rows,
    })


def normalize_chart_field_references(plan: Any) -> Any:
    """标准化 ChartPlan 中的横轴、序列和数值字段引用。

    这是兼容边界：旧模型偶尔仍会提交 ``power_kwh`` 或 ``time``，
    Runtime/图表内部会先转换为标准字段再校验，避免同义字段造成无意义重试。
    """

    updates: dict[str, Any] = {}
    for field_name in ("x_field", "group_field", "value_field"):
        value = getattr(plan, field_name, None)
        if value:
            updates[field_name] = canonical_chart_field_name(value)

    series = []
    for binding in getattr(plan, "series", []) or []:
        field = canonical_chart_field_name(binding.field)
        if field == binding.field:
            series.append(binding)
        else:
            series.append(binding.model_copy(update={"field": field}))
    updates["series"] = series
    return plan.model_copy(update=updates)
