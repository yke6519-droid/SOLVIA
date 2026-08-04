"""把图表请求和数据制品解析为图表数据视图。"""

from __future__ import annotations

from typing import Any

from backend.app.charting.errors import ChartValidationError
from backend.app.charting.field_names import TIMESTAMP_FIELD, VALUE_KWH_FIELD
from backend.app.charting.schemas import ChartDataView, ChartRequest, DatasetArtifact


def _distinct_count(rows: list[dict[str, Any]], field_name: str) -> int:
    """统计数据行中的有效序列数量。"""

    return len({str(row[field_name]) for row in rows if row.get(field_name) is not None})


def _declared_count(metadata: dict[str, Any], key: str, rows: list[dict[str, Any]], field_name: str) -> int:
    """优先使用制品元数据，没有声明时再从数据行推断。"""

    value = metadata.get(key)
    if isinstance(value, int) and value > 0:
        return value
    return _distinct_count(rows, field_name)


def _resolve_series_field(
    request: ChartRequest,
    artifact: DatasetArtifact,
) -> str | None:
    """根据站点和数据源数量确定图表序列字段。"""

    metadata = artifact.metadata
    station_count = _declared_count(metadata, "station_count", artifact.rows, "station")
    source_count = len(request.source_types)
    if source_count <= 1:
        source_count = max(source_count, _distinct_count(artifact.rows, "source_type"))

    if station_count <= 1 and source_count <= 1:
        return None

    declared_dimension = metadata.get("series_dimension")
    if isinstance(declared_dimension, str) and declared_dimension in artifact.field_schema:
        return declared_dimension

    candidates = ["station", "series_key"] if station_count > 1 else ["source_type", "series_key"]
    for field_name in candidates:
        if field_name in artifact.field_schema:
            return field_name
    raise ChartValidationError(
        "SERIES_FIELD_INFERENCE_FAILED",
        "无法根据数据制品确定图表序列字段",
        details={"available_fields": list(artifact.field_schema)},
    )


def resolve_chart_data(request: ChartRequest, artifact: DatasetArtifact) -> ChartDataView:
    """解析图表数据视图，不访问数据库，也不修改原始数据制品。"""

    x_field = "date" if request.granularity == "daily_total" else TIMESTAMP_FIELD
    if x_field not in artifact.field_schema:
        raise ChartValidationError(
            "X_FIELD_INFERENCE_FAILED",
            f"图表数据制品缺少横轴字段: {x_field}",
            details={"available_fields": list(artifact.field_schema)},
        )
    if VALUE_KWH_FIELD not in artifact.field_schema:
        raise ChartValidationError(
            "VALUE_FIELD_INFERENCE_FAILED",
            f"图表数据制品缺少标准数值字段: {VALUE_KWH_FIELD}",
            details={"available_fields": list(artifact.field_schema)},
        )

    return ChartDataView(
        artifact_id=artifact.artifact_id,
        field_schema=artifact.field_schema,
        rows=list(artifact.rows),
        x_field=x_field,
        x_role="date" if x_field == "date" else "time",
        value_field=VALUE_KWH_FIELD,
        series_field=_resolve_series_field(request, artifact),
        metadata=dict(artifact.metadata),
    )
