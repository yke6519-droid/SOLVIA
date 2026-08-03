"""Thin Agent-facing wrappers for the structured chart pipeline."""

import json
from datetime import date
from typing import Annotated
from uuid import uuid4

from langchain_core.tools import tool
from pydantic import Field

from backend.app.charting.context import (
    capability_preflight_done,
    consume_chart_plan_attempt,
    mark_capability_preflight,
)
from backend.app.services.dataset_artifact_service import (
    get_dataset_context,
    save_dataset_artifact,
)
from backend.app.charting.datasets import (
    DatasetSourceError,
    load_power_source,
    load_power_source_range,
    normalize_source_types,
)
from backend.app.charting.errors import ChartValidationError
from backend.app.charting.registry import get_capability, get_enabled_capabilities
from backend.app.charting.schemas import ChartPlan, FieldDefinition, SeriesBinding
from backend.app.charting.service import chart_service
from backend.app.errors import ToolError


SOURCE_LABELS = {"actual": "实际发电量", "predicted": "预测发电量"}


def _ensure_capability_preflight() -> dict:
    """Ensure the current chart flow has inspected the capability registry.

    Prompt ordering is only a hint. If the Agent skips the public
    ``get_chart_capabilities`` tool, the deterministic tool layer repairs the
    missing transition instead of exposing an orchestration error to the user.
    """

    if capability_preflight_done():
        return {"status": "ready", "mode": "agent_preflight"}

    capabilities = get_enabled_capabilities()
    mark_capability_preflight()
    return {
        "status": "ready",
        "mode": "auto_preflight",
        "capability_count": len(capabilities),
    }


def _binding_hints(
    *,
    station_count: int,
    source_count: int,
    is_daily: bool,
    period_start: str,
    period_end: str,
    series_dimension: str,
) -> dict:
    """Derive semantic binding candidates from artifact shape, not station names."""

    compare_capability = get_capability("time_series_compare")
    aggregate_capability = get_capability("period_aggregate")
    multi_day = period_start != period_end
    candidates: list[dict] = []
    unsupported: list[str] = []

    if is_daily:
        if source_count > 1:
            unsupported.append("daily_total 周期汇总第一阶段只允许一种数据来源")
        elif station_count > 1 and multi_day:
            if aggregate_capability and station_count <= aggregate_capability.max_series:
                candidates.append({
                    "capability_id": "period_aggregate",
                    "template_id": "aggregate_bar",
                    "chart_type": "bar",
                    "x_field": "date",
                    "x_role": "date",
                    "series_mode": "group",
                    "group_field": series_dimension,
                    "value_field": "value_kwh",
                    "view": "date_trend",
                    "series_count": station_count,
                    "max_series": aggregate_capability.max_series,
                })
            else:
                unsupported.append(
                    f"站点数量 {station_count} 超过周期分组柱状图上限 "
                    f"{aggregate_capability.max_series if aggregate_capability else 0}"
                )
        elif station_count > 1:
            candidates.append({
                "capability_id": "period_aggregate",
                "template_id": "aggregate_bar",
                "chart_type": "bar",
                "x_field": "station",
                "x_role": "station",
                "series_mode": "single",
                "view": "station_snapshot",
                "series_count": 1,
                "max_series": aggregate_capability.max_series if aggregate_capability else 0,
            })
        else:
            candidates.append({
                "capability_id": "period_aggregate",
                "template_id": "aggregate_bar",
                "chart_type": "bar",
                "x_field": "date",
                "x_role": "date",
                "series_mode": "single",
                "view": "date_trend",
                "series_count": 1,
                "max_series": aggregate_capability.max_series if aggregate_capability else 0,
            })
    elif station_count == 1 and source_count == 1:
        candidates.append({
            "capability_id": "time_series_trend",
            "template_id": "single_line",
            "chart_type": "line",
            "x_field": "timestamp",
            "x_role": "time",
            "series_mode": "single",
            "series_count": 1,
            "max_series": 1,
        })
    elif station_count > 1 and source_count > 1:
        unsupported.append("多站点逐小时对比第一阶段只能选择一种数据来源")
    elif compare_capability and max(station_count, source_count) <= compare_capability.max_series and (
        station_count > 1 or source_count > 1
    ):
        candidates.append({
            "capability_id": "time_series_compare",
            "template_id": "multi_line",
            "chart_type": "line",
            "x_field": "timestamp",
            "x_role": "time",
            "series_mode": "group",
            "group_field": series_dimension,
            "value_field": "value_kwh",
            "series_count": max(station_count, source_count),
            "max_series": compare_capability.max_series,
            "max_points_per_series": compare_capability.max_points_per_series,
            "max_total_points": compare_capability.max_total_points,
        })
    else:
        unsupported.append("逐小时对比序列数量超过当前能力上限")

    return {
        "candidate_capabilities": candidates,
        "unsupported_reasons": unsupported,
        "series_count": station_count if station_count > 1 else source_count,
        "max_series": max(
            item.max_series
            for item in (compare_capability, aggregate_capability)
            if item is not None
        ),
    }


def _station_targets(station_name: str | None, station_names: list[str] | None) -> list[str]:
    values = list(station_names or [])
    if station_name:
        values.insert(0, station_name)
    values = [str(value).strip() for value in values if str(value).strip()]
    if not values:
        raise ToolError("STATION_REQUIRED", "至少提供一个站点名称")
    return list(dict.fromkeys(values))


def _validate_range_shape(
    *,
    station_count: int,
    source_count: int,
    granularity: str,
    period_start: str,
    period_end: str,
) -> dict:
    """Validate a range request before any station/database lookup.

    A date range means complete natural days.  ``hourly`` therefore expands
    to 24 points per day (00:00 through 23:00); callers do not provide a
    separate time-of-day range.  The limits come from the capability registry
    so the tool remains extensible when a new capability is registered.
    """

    period_days = (date.fromisoformat(period_end) - date.fromisoformat(period_start)).days + 1
    if period_days <= 0:
        raise ToolError("DATA_RANGE_INVALID", "日期范围必须至少包含一天")

    if granularity == "hourly":
        if station_count > 1 and source_count > 1:
            raise ToolError("DATA_SOURCE_COMBINATION_UNSUPPORTED", "第一阶段暂不支持多站点同时展开实际和预测序列")
        capability_id = (
            "time_series_trend"
            if station_count == 1 and source_count == 1
            else "time_series_compare"
        )
        points_per_series = period_days * 24
        series_count = max(station_count, source_count)
    elif granularity == "daily_total":
        if source_count > 1:
            raise ToolError("DATA_SOURCE_COMBINATION_UNSUPPORTED", "周期汇总第一阶段只支持一种数据来源")
        capability_id = "period_aggregate"
        points_per_series = period_days
        series_count = station_count
    else:
        raise ToolError("DATA_GRANULARITY_UNSUPPORTED", "不支持的日期范围数据粒度")

    capability = get_capability(capability_id)
    if capability is None:
        raise ToolError("CAPABILITY_NOT_ENABLED", f"当前未启用图表能力: {capability_id}")
    if series_count < capability.min_series or series_count > capability.max_series:
        raise ToolError(
            "DATA_SERIES_LIMIT_EXCEEDED",
            f"序列数量 {series_count} 超出 {capability_id} 的允许范围 "
            f"{capability.min_series}~{capability.max_series}"
        )
    if points_per_series > capability.max_points_per_series:
        raise ToolError(
            "DATA_POINT_LIMIT_EXCEEDED",
            f"日期范围展开后每条序列有 {points_per_series} 个点，"
            f"超过 {capability_id} 的上限 {capability.max_points_per_series}；"
            "请缩短日期范围"
        )
    total_points = points_per_series * series_count
    if total_points > capability.max_total_points:
        raise ToolError(
            "DATA_POINT_LIMIT_EXCEEDED",
            f"日期范围展开后共有 {total_points} 个点，"
            f"超过 {capability_id} 的总点数上限 {capability.max_total_points}；"
            "请缩短日期范围或减少站点/序列"
        )
    return {
        "capability_id": capability_id,
        "period_days": period_days,
        "points_per_series": points_per_series,
        "series_count": series_count,
        "total_points": total_points,
    }


@tool
def get_power_dataset(
    target_date: Annotated[str | None, "单日日期；范围汇总时改用 start_date 和 end_date"] = None,
    station_name: Annotated[str | None, "单个站点名称，例如‘英杰’；多站点时可改用 station_names"] = None,
    station_names: Annotated[list[str] | None, "多个站点名称，例如['英杰','哲丰一车间']"] = None,
    source_types: Annotated[list[str] | None, "数据来源列表：actual实际、predicted预测；单站点对比可同时传两项"] = None,
    start_date: Annotated[str | None, "周期汇总起始日期"] = None,
    end_date: Annotated[str | None, "周期汇总结束日期"] = None,
    granularity: Annotated[str, "数据粒度：hourly逐小时，daily_total日总量"] = "hourly",
) -> str:
    """获取并标准化发电数据制品，不选择图表模板。"""
    from backend.app.charting.schemas import DatasetArtifact
    from backend.tools.date_parser_tool import parse_flexible_date
    from backend.tools.power_query_tool import _resolve_station_id

    # 获取当前用户与会话
    dataset_context = get_dataset_context()
    # 确保能力预检已完成，返回当前注册能力和限制
    capability_preflight = _ensure_capability_preflight()
    # 解析站点名称列表，去重并按顺序保留
    targets = _station_targets(station_name, station_names)

    try:
        # 解析并验证数据来源类型，默认使用实际发电量
        requested_sources = normalize_source_types(source_types)
    except DatasetSourceError as exc:
        raise ToolError("DATA_SOURCE_UNSUPPORTED", str(exc)) from exc

    if granularity not in {"hourly", "daily_total"}:
        raise ToolError("DATA_GRANULARITY_UNSUPPORTED", "不支持的数据粒度。可选值：hourly、daily_total")
    is_range_request = bool(start_date or end_date)
    range_shape = None
    if is_range_request:
        if not start_date or not end_date:
            raise ToolError("DATA_RANGE_INVALID", "周期汇总必须同时提供 start_date 和 end_date")
        period_start = parse_flexible_date(start_date)
        period_end = parse_flexible_date(end_date)
        if period_start > period_end:
            raise ToolError("DATA_RANGE_INVALID", "start_date 不能晚于 end_date")
        range_shape = _validate_range_shape(
            station_count=len(targets),
            source_count=len(requested_sources),
            granularity=granularity,
            period_start=period_start,
            period_end=period_end,
        )
    else:
        if not target_date:
            raise ToolError("DATA_RANGE_INVALID", "必须提供 target_date，或同时提供 start_date 和 end_date")
        period_start = period_end = parse_flexible_date(target_date)

    is_daily = granularity == "daily_total"
    axis_field = "date" if is_daily else "timestamp"
    rows: list[dict] = []
    station_infos: list[dict] = []
    source_labels: dict[str, str] = {}

    # 循环处理每个站点和数据来源，获取发电数据并构建标准化行
    for target in targets:
        station_id, station_info = _resolve_station_id(target)
        station_infos.append({"id": station_id, **station_info})
        for source_type in requested_sources:
            try:
                if is_range_request:
                    frame = load_power_source_range(source_type, station_id, period_start, period_end)
                    if is_daily:
                        frame["date"] = frame["timestamp"].dt.strftime("%Y-%m-%d")
                        frame = frame.groupby("date", as_index=False)["value_kwh"].sum().sort_values("date")
                else:
                    frame = load_power_source(source_type, station_id, period_start)
            except DatasetSourceError as exc:
                raise ToolError("DATA_NOT_FOUND", str(exc)) from exc

            source_labels[source_type] = SOURCE_LABELS.get(source_type, source_type)
            for _, value in frame.iterrows():
                axis_value = value[axis_field]
                if hasattr(axis_value, "isoformat"):
                    axis_value = axis_value.isoformat(sep=" ")
                rows.append({
                    axis_field: axis_value,
                    "station": station_info["name"],
                    "source_type": source_type,
                    # The semantic series key is source for one station and
                    # station for a multi-station query.
                    "series_key": source_type if len(targets) == 1 else station_info["name"],
                    "value_kwh": float(value["value_kwh"]),
                })

    if not rows:
        raise ToolError("DATA_NOT_FOUND", f"{period_start} 至 {period_end} 未找到可用于绘图的发电数据")

    single_station = len(targets) == 1
    series_dimension = "source_type" if single_station and len(requested_sources) > 1 else (
        "station" if len(targets) > 1 else "source_type"
    )
    data_type = requested_sources[0] if len(requested_sources) == 1 else "comparison"
    artifact = DatasetArtifact(
        artifact_id=f"artifact_{uuid4().hex[:12]}",
        artifact_type="daily_aggregate" if is_daily else "hourly_series",
        owner_user_id=dataset_context.user_id,
        session_id=dataset_context.session_id,
        schema={
            axis_field: FieldDefinition(
                type="date" if is_daily else "datetime",
                role="date" if is_daily else "time",
                label="日期" if is_daily else "时间",
            ),
            "station": FieldDefinition(type="category", role="category", label="站点"),
            "source_type": FieldDefinition(type="category", role="category", label="数据来源"),
            "series_key": FieldDefinition(type="category", role="category", label="图表序列"),
            "value_kwh": FieldDefinition(type="number", role="measure", label="发电量", unit="kWh"),
        },
        rows=sorted(rows, key=lambda row: (str(row[axis_field]), str(row["series_key"]))),
        metadata={
            "date": period_start if period_start == period_end else None,
            "period_start": period_start,
            "period_end": period_end,
            "range_semantics": (
                "每个自然日00:00-23:00"
                if is_range_request and not is_daily
                else "按自然日汇总"
                if is_range_request
                else None
            ),
            "period_days": range_shape["period_days"] if range_shape else 1,
            "granularity": granularity,
            "source_types": requested_sources,
            "source_type": requested_sources[0] if len(requested_sources) == 1 else None,
            "data_type": data_type,
            "series_dimension": series_dimension,
            "station_count": len(targets),
            "stations": [item["name"] for item in station_infos],
            "station": station_infos[0]["name"] if single_station else "多站点",
            "station_full_name": station_infos[0]["name"] if single_station else "多站点",
            "series_labels": {
                **source_labels,
                **({item["name"]: item["name"] for item in station_infos} if len(targets) > 1 else {}),
            },
            "source_tool": "get_power_dataset",
        },
    )
    save_dataset_artifact(artifact)
    return json.dumps(
        {
            "status": "created",
            "artifact_id": artifact.artifact_id,
            "artifact_type": artifact.artifact_type,
            "row_count": len(rows),
            "station_count": len(targets),
            "source_types": requested_sources,
            "schema": {
                field_name: field.model_dump()
                for field_name, field in artifact.field_schema.items()
            },
            "chart_binding_hints": {
                "capability_preflight": capability_preflight,
                "time_fields": ["timestamp"] if not is_daily else [],
                "date_fields": ["date"] if is_daily else [],
                "category_fields": ["station", "source_type", "series_key"],
                "measure_fields": ["value_kwh"],
                "series_dimension": series_dimension,
                "recommended_bindings": _binding_hints(
                    station_count=len(targets),
                    source_count=len(requested_sources),
                    is_daily=is_daily,
                    period_start=period_start,
                    period_end=period_end,
                    series_dimension=series_dimension,
                ),
            },
        },
        ensure_ascii=False,
    )


class CreateChartPlanArgs(ChartPlan):
    """Stable tool schema; capability-specific rules remain in the registry."""

    series: list[SeriesBinding] = Field(default_factory=list)


@tool(args_schema=CreateChartPlanArgs)
def create_chart_plan(
    capability_id: str,
    artifact_id: str,
    chart_type: str | None = None,
    template_id: str | None = None,
    x_field: str | None = None,
    x_role: str | None = None,
    series_mode: str | None = None,
    series: list[dict] | None = None,
    group_field: str | None = None,
    value_field: str | None = None,
    view: str | None = None,
    title: str | None = None,
    schema_version: str = "1.0",
) -> str:
    """提交受限 ChartPlan，成功后返回后端生成的 ChartSpec。"""
    capability_preflight = _ensure_capability_preflight()

    attempt, max_attempts = consume_chart_plan_attempt()
    if attempt > max_attempts:
        return json.dumps(
            {
                "status": "recoverable_error",
                "code": "CHART_PLAN_ATTEMPTS_EXCEEDED",
                "message": "本次请求的图表计划修正次数已用尽",
                "data": {"attempt": attempt, "max_attempts": max_attempts},
                "retryable": False,
                "suggested_actions": [],
                "artifact_ids": [],
            },
            ensure_ascii=False,
        )

    plan = ChartPlan(
        schema_version=schema_version,
        capability_id=capability_id,
        chart_type=chart_type,
        template_id=template_id,
        artifact_id=artifact_id,
        x_field=x_field,
        x_role=x_role,
        series_mode=series_mode,
        series=series or [],
        group_field=group_field,
        value_field=value_field,
        view=view,
        title=title,
    )
    try:
        chart_spec = chart_service.create_chart(plan)
    except ChartValidationError as exc:
        error = exc.to_dict()
        error["retryable"] = attempt < max_attempts
        error.setdefault("details", {})["attempt"] = attempt
        error["details"]["max_attempts"] = max_attempts
        return json.dumps(
            {
                "status": "recoverable_error",
                "code": error["code"],
                "message": error["message"],
                "data": {
                    "capability_preflight": capability_preflight,
                    "details": error["details"],
                },
                "retryable": error["retryable"],
                "suggested_actions": [],
                "artifact_ids": [],
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "status": "success",
            "code": None,
            "message": "图表已生成",
            "data": {
                "capability_preflight": capability_preflight,
                "chart_spec": chart_spec.model_dump(mode="json"),
            },
            "retryable": False,
            "suggested_actions": [],
            "artifact_ids": [],
        },
        ensure_ascii=False,
    )


@tool
def get_chart_capabilities() -> str:
    """返回当前运行时已启用的图表能力和限制。"""
    mark_capability_preflight()
    return json.dumps(
        {
            "capabilities": get_enabled_capabilities(),
            "selection_rule": "先根据数据粒度、序列数量和字段角色选择已注册能力，再获取数据制品并提交 ChartPlan",
        },
        ensure_ascii=False,
    )
