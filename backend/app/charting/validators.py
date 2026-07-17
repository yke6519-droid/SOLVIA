"""Deterministic validation for ChartPlan and DatasetArtifact."""

import math
from datetime import datetime

from backend.app.charting.errors import ChartValidationError
from backend.app.charting.registry import CapabilityDefinition, get_capability, get_enabled_capabilities
from backend.app.charting.schemas import ChartPlan, DatasetArtifact


def _require_field(artifact: DatasetArtifact, field: str, *, role: str) -> None:
    if field not in artifact.field_schema:
        raise ChartValidationError(
            "FIELD_NOT_FOUND",
            f"{role}字段不存在: {field}",
            details={"field": field, "available": list(artifact.field_schema)},
        )


def _finite_number(value, field: str, row_index: int) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ChartValidationError(
            "INVALID_NUMBER",
            f"字段 {field} 包含无法转为数值的数据",
            details={"row_index": row_index, "value": value},
        ) from exc
    if not math.isfinite(number):
        raise ChartValidationError(
            "INVALID_NUMBER",
            f"字段 {field} 包含 NaN 或 Infinity",
            details={"row_index": row_index, "value": value},
        )
    return number


def _validate_time_values(artifact: DatasetArtifact, field: str, rows=None) -> None:
    rows = artifact.rows if rows is None else rows
    previous = None
    seen = set()
    for index, row in enumerate(rows):
        value = row.get(field)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ChartValidationError(
                "INVALID_TIME_AXIS",
                f"时间轴字段 {field} 包含无法解析的时间",
                details={"row_index": index, "value": value},
            ) from exc
        if parsed in seen:
            raise ChartValidationError(
                "DUPLICATE_TIME_POINT",
                f"时间轴字段 {field} 存在重复时间点",
                details={"value": str(value)},
            )
        if previous is not None and parsed < previous:
            raise ChartValidationError(
                "UNSORTED_TIME_AXIS",
                f"时间轴字段 {field} 未按时间升序排列",
            )
        seen.add(parsed)
        previous = parsed


def _validate_common(plan: ChartPlan, artifact: DatasetArtifact, capability: CapabilityDefinition) -> None:
    if not artifact.rows:
        raise ChartValidationError("NO_DATA", "图表数据制品为空，无法生成图表")
    if plan.chart_type not in capability.chart_types:
        raise ChartValidationError("CHART_TYPE_NOT_ALLOWED", "当前能力不支持该图表类型")
    if plan.template_id not in capability.templates:
        raise ChartValidationError("TEMPLATE_NOT_ALLOWED", "当前能力不支持该图表模板")
    if plan.x_role not in capability.x_roles:
        raise ChartValidationError("X_ROLE_NOT_ALLOWED", "横轴类型不在当前能力范围内")
    if plan.series_mode not in capability.series_modes:
        raise ChartValidationError("SERIES_MODE_NOT_ALLOWED", "序列组织方式不在当前能力范围内")
    if capability.allowed_views and plan.view not in capability.allowed_views:
        raise ChartValidationError(
            "VIEW_NOT_ALLOWED",
            "当前图表能力不支持该视图",
            details={"allowed_views": list(capability.allowed_views)},
        )

    granularity = str(artifact.metadata.get("granularity", ""))
    if granularity not in capability.allowed_granularities:
        raise ChartValidationError(
            "GRANULARITY_NOT_ALLOWED",
            f"当前能力不支持数据粒度: {granularity or '未声明'}",
            details={"allowed": list(capability.allowed_granularities)},
        )

    point_limit = capability.max_total_points if plan.series_mode == "group" else capability.max_points_per_series
    if len(artifact.rows) > point_limit:
        raise ChartValidationError(
            "POINT_LIMIT_EXCEEDED",
            "数据点数量超过当前图表能力上限",
            details={"max_points": point_limit},
        )

    _require_field(artifact, plan.x_field, role="横轴")
    if artifact.field_schema[plan.x_field].type not in {"datetime", "date", "category"}:
        raise ChartValidationError("INVALID_X_FIELD", "横轴字段必须是时间、日期或类别类型")


def validate_chart_plan(plan: ChartPlan, artifact: DatasetArtifact) -> None:
    capability = get_capability(plan.capability_id)
    if capability is None:
        raise ChartValidationError(
            "CAPABILITY_NOT_ENABLED",
            f"图表能力未启用: {plan.capability_id}",
            details={"allowed": [item["capability_id"] for item in get_enabled_capabilities()]},
        )

    _validate_common(plan, artifact, capability)

    if plan.series_mode == "group":
        if not plan.group_field or not plan.value_field:
            raise ChartValidationError("GROUP_BINDING_INCOMPLETE", "分组序列必须提供 group_field 和 value_field")
        _require_field(artifact, plan.group_field, role="分组")
        _require_field(artifact, plan.value_field, role="数值")
        if artifact.field_schema[plan.group_field].type != "category":
            raise ChartValidationError("INVALID_GROUP_FIELD", "分组字段必须是类别类型")
        if artifact.field_schema[plan.value_field].type != "number":
            raise ChartValidationError("INVALID_VALUE_FIELD", "数值字段必须是 number 类型")
        for row_index, row in enumerate(artifact.rows):
            _finite_number(row.get(plan.value_field), plan.value_field, row_index)
        groups = {row.get(plan.group_field) for row in artifact.rows}
        if not capability.min_series <= len(groups) <= capability.max_series:
            raise ChartValidationError(
                "GROUP_COUNT_EXCEEDED",
                "分组数量不在当前能力范围内",
                details={"count": len(groups), "max_groups": capability.max_series},
            )
    else:
        if plan.series_mode == "single" and len(plan.series) != 1:
            raise ChartValidationError("SERIES_COUNT_INVALID", "单序列图必须绑定且只能绑定一个数据序列")
        fields = [item.field for item in plan.series]
        if not capability.min_series <= len(fields) <= capability.max_series:
            raise ChartValidationError(
                "SERIES_LIMIT_EXCEEDED",
                "图表序列数量不在当前能力范围内",
                details={"min_series": capability.min_series, "max_series": capability.max_series},
            )
        for binding in plan.series:
            _require_field(artifact, binding.field, role="序列")
            if artifact.field_schema[binding.field].type != "number":
                raise ChartValidationError("INVALID_SERIES_FIELD", f"序列字段必须是 number 类型: {binding.field}")
            for row_index, row in enumerate(artifact.rows):
                _finite_number(row.get(binding.field), binding.field, row_index)
        if len(artifact.rows) * len(fields) > capability.max_total_points:
            raise ChartValidationError("TOTAL_POINT_LIMIT_EXCEEDED", "图表总数据点数量超过上限")

    if plan.capability_id == "time_series_trend":
        if plan.x_role != "time" or plan.series_mode != "single":
            raise ChartValidationError("TREND_PLAN_INVALID", "time_series_trend 只支持单序列时间折线")
        _validate_time_values(artifact, plan.x_field)

    if plan.capability_id == "time_series_compare":
        if plan.x_role != "time":
            raise ChartValidationError("COMPARE_PLAN_INVALID", "time_series_compare 必须使用时间横轴")
        if plan.series_mode == "group":
            station_values = {
                str(row.get("station"))
                for row in artifact.rows
                if row.get("station") not in (None, "")
            }
            source_values = {
                str(row.get("source_type"))
                for row in artifact.rows
                if row.get("source_type") not in (None, "")
            }
            station_count = artifact.metadata.get("station_count")
            is_multi_station = len(station_values) > 1 or (
                isinstance(station_count, int) and station_count > 1
            )
            if is_multi_station and len(source_values) > 1:
                raise ChartValidationError(
                    "MULTI_STATION_SOURCE_MIXED",
                    "多站点对比图只能选择实际或预测一种数据来源，不能混合展示",
                    details={
                        "stations": sorted(station_values),
                        "source_types": sorted(source_values),
                    },
                )

            groups: dict[str, list[dict]] = {}
            for row in artifact.rows:
                groups.setdefault(str(row.get(plan.group_field)), []).append(row)
            for group_rows in groups.values():
                _validate_time_values(
                    artifact,
                    plan.x_field,
                    rows=sorted(group_rows, key=lambda row: str(row.get(plan.x_field))),
                )

        else:
            _validate_time_values(artifact, plan.x_field)

    if plan.capability_id == "period_aggregate":
        if plan.chart_type != "bar":
            raise ChartValidationError("AGGREGATE_PLAN_INVALID", "period_aggregate 只支持柱状图")
        source_values = {
            str(row.get("source_type"))
            for row in artifact.rows
            if row.get("source_type") not in (None, "")
        }
        if len(source_values) > 1:
            raise ChartValidationError(
                "AGGREGATE_SOURCE_MIXED",
                "周期汇总柱状图第一版只支持一种数据来源",
                details={"source_types": sorted(source_values)},
            )

        station_count = artifact.metadata.get("station_count")
        is_multi_station = isinstance(station_count, int) and station_count > 1
        period_start = artifact.metadata.get("period_start")
        period_end = artifact.metadata.get("period_end")
        is_multi_day = period_start is not None and period_end is not None and period_start != period_end

        # A multi-station, multi-day artifact must remain grouped by its
        # semantic series dimension. Otherwise the Builder would flatten all
        # rows into one repeated-date series and silently lose station meaning.
        if is_multi_station and is_multi_day and plan.series_mode != "group":
            raise ChartValidationError(
                "AGGREGATE_GROUP_REQUIRED",
                "多站点多日汇总必须使用 group 模式，并按数据制品的分组维度生成序列",
                details={
                    "required_group_field": artifact.metadata.get("series_dimension", "station"),
                    "required_value_field": "value_kwh",
                    "required_x_field": "date",
                    "retryable": True,
                },
            )

        if plan.series_mode == "group":
            if plan.view != "date_trend" or plan.x_role != "date":
                raise ChartValidationError(
                    "AGGREGATE_GROUP_VIEW_INVALID",
                    "多站点周期分组柱状图必须使用 date_trend 和 date 横轴",
                )
            expected_group = artifact.metadata.get("series_dimension")
            if expected_group and plan.group_field != expected_group:
                raise ChartValidationError(
                    "AGGREGATE_GROUP_BINDING_INVALID",
                    "分组字段必须使用数据制品声明的序列维度",
                    details={"expected_group_field": expected_group},
                )

        if plan.view == "station_snapshot":
            if plan.x_role != "station":
                raise ChartValidationError("AGGREGATE_VIEW_INVALID", "站点快照必须使用 station 横轴")
            if is_multi_day:
                raise ChartValidationError("STATION_SNAPSHOT_NOT_SINGLE_DAY", "站点快照必须对应单日汇总")
        elif plan.view == "date_trend":
            if plan.x_role != "date":
                raise ChartValidationError("AGGREGATE_VIEW_INVALID", "日期趋势必须使用 date 横轴")
        else:
            raise ChartValidationError("AGGREGATE_VIEW_REQUIRED", "period_aggregate 必须声明 date_trend 或 station_snapshot")
