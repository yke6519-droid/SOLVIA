"""Pure ChartPlan + DatasetArtifact to ChartSpec builders."""

from datetime import datetime
from uuid import uuid4

from backend.app.charting.schemas import ChartPlan, ChartSpec, DatasetArtifact, ChartSeries


SERIES_COLORS = ("#2E86C1", "#E67E22", "#2E8B57", "#8E44AD")
SOURCE_LABELS = {"actual": "实际发电量", "predicted": "预测发电量"}


def _format_axis_value(value, role: str) -> str:
    if role == "time":
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M")
    return str(value)


def build_single_line(plan: ChartPlan, artifact: DatasetArtifact) -> ChartSpec:
    rows = sorted(artifact.rows, key=lambda row: str(row.get(plan.x_field)))
    binding = plan.series[0]
    field_def = artifact.field_schema[binding.field]
    values = [float(row[binding.field]) for row in rows]
    labels = [_format_axis_value(row[plan.x_field], plan.x_role) for row in rows]
    peak_index = max(range(len(values)), key=lambda index: values[index])
    source_type = artifact.metadata.get("source_type") or artifact.metadata.get("data_type", "actual")
    metadata = {
        **artifact.metadata,
        "artifact_id": artifact.artifact_id,
        "point_count": len(rows),
        "source_type": source_type,
        "total_kwh": round(sum(values), 2),
        "peak_time": labels[peak_index],
        "peak_value_kwh": round(values[peak_index], 2),
        "generation_hours": sum(1 for value in values if value > 0),
    }
    # Preserve the old metadata keys for snapshots and existing frontend code.
    prefix = "predicted" if source_type == "predicted" else "actual"
    metadata.update({
        f"{prefix}_total_kwh": metadata["total_kwh"],
        f"{prefix}_peak_time": metadata["peak_time"],
        f"{prefix}_peak_value_kwh": metadata["peak_value_kwh"],
        f"{prefix}_generation_hours": metadata["generation_hours"],
    })
    return ChartSpec(
        chart_id=f"chart_{uuid4().hex[:12]}",
        capability_id=plan.capability_id,
        template_id=plan.template_id,
        chart_type="line",
        title=plan.title or f"{artifact.metadata.get('station_full_name', '站点')}逐小时发电量",
        x_axis={"role": plan.x_role, "label": "时间", "data": labels},
        y_axis={"label": field_def.label, "unit": field_def.unit},
        series=[ChartSeries(
            name=binding.name or field_def.label,
            data=values,
            unit=field_def.unit,
            color="#2E86C1",
        )],
        metadata=metadata,
    )


def _ordered_time_values(rows: list[dict], field: str) -> list:
    return sorted({row.get(field) for row in rows}, key=lambda value: str(value))


def _display_series_name(value, artifact: DatasetArtifact) -> str:
    labels = artifact.metadata.get("series_labels") or {}
    value_text = str(value)
    return str(labels.get(value_text) or SOURCE_LABELS.get(value_text) or value_text)


def build_multi_line(plan: ChartPlan, artifact: DatasetArtifact) -> ChartSpec:
    """Build a line chart from long-form rows grouped by a semantic field."""

    rows = sorted(artifact.rows, key=lambda row: str(row.get(plan.x_field)))
    x_values = _ordered_time_values(rows, plan.x_field)
    grouped: dict[str, dict[str, float]] = {}
    for row in rows:
        group = str(row.get(plan.group_field))
        grouped.setdefault(group, {})[str(row.get(plan.x_field))] = float(row[plan.value_field])

    field_def = artifact.field_schema[plan.value_field]
    series = [
        ChartSeries(
            name=_display_series_name(group, artifact),
            data=[values.get(str(x_value)) for x_value in x_values],
            unit=field_def.unit,
            color=SERIES_COLORS[index % len(SERIES_COLORS)],
        )
        for index, (group, values) in enumerate(grouped.items())
    ]
    metadata = {
        **artifact.metadata,
        "artifact_id": artifact.artifact_id,
        "point_count": len(rows),
        "series_count": len(series),
        "series_names": [item.name for item in series],
    }
    return ChartSpec(
        chart_id=f"chart_{uuid4().hex[:12]}",
        capability_id=plan.capability_id,
        template_id=plan.template_id,
        chart_type="line",
        title=plan.title or f"{artifact.metadata.get('date', '')}发电量对比",
        x_axis={
            "role": plan.x_role,
            "label": "时间",
            "data": [_format_axis_value(value, plan.x_role) for value in x_values],
        },
        y_axis={"label": field_def.label, "unit": field_def.unit},
        series=series,
        metadata=metadata,
    )


def build_aggregate_bar(plan: ChartPlan, artifact: DatasetArtifact) -> ChartSpec:
    """Build a daily-total bar chart from one or more semantic series.

    A multi-station period artifact uses long-form rows and ``group_mode``;
    each actual group becomes one ChartSeries. The number of series therefore
    comes from the data, while the registry only supplies the upper bound.
    """

    rows = sorted(artifact.rows, key=lambda row: str(row.get(plan.x_field)))
    value_field = plan.value_field if plan.series_mode == "group" else plan.series[0].field
    field_def = artifact.field_schema[value_field]
    x_values = _ordered_time_values(rows, plan.x_field)
    labels = [_format_axis_value(value, plan.x_role) for value in x_values]

    if plan.series_mode == "group":
        grouped: dict[str, dict[str, float]] = {}
        for row in rows:
            group = str(row.get(plan.group_field))
            x_value = str(row.get(plan.x_field))
            grouped.setdefault(group, {})[x_value] = float(row[value_field])
        series = [
            ChartSeries(
                name=_display_series_name(group, artifact),
                data=[values.get(str(x_value)) for x_value in x_values],
                unit=field_def.unit,
                color=SERIES_COLORS[index % len(SERIES_COLORS)],
            )
            for index, (group, values) in enumerate(grouped.items())
        ]
        values = [value for item in series for value in item.data if value is not None]
        series_names = [item.name for item in series]
    else:
        binding = plan.series[0]
        values = [float(row[binding.field]) for row in rows]
        series_names = [binding.name or field_def.label]
        series = [ChartSeries(
            name=binding.name or field_def.label,
            data=values,
            unit=field_def.unit,
            color="#2E8B78",
        )]

    metadata = {
        **artifact.metadata,
        "artifact_id": artifact.artifact_id,
        "point_count": len(rows),
        "series_count": len(series),
        "series_names": series_names,
        "total_kwh": round(sum(values), 2),
    }
    return ChartSpec(
        chart_id=f"chart_{uuid4().hex[:12]}",
        capability_id=plan.capability_id,
        template_id=plan.template_id,
        chart_type="bar",
        title=plan.title or f"{artifact.metadata.get('station', '站点')}日总发电量",
        x_axis={"role": plan.x_role, "label": "日期" if plan.x_role == "date" else "站点", "data": labels},
        y_axis={"label": field_def.label, "unit": field_def.unit},
        series=series,
        metadata=metadata,
    )
