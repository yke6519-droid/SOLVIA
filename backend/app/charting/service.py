"""Application service for the structured chart pipeline."""

from backend.app.charting.builders import build_aggregate_bar, build_multi_line, build_single_line
from backend.app.charting.context import get_chart_context
from backend.app.charting.errors import ChartValidationError
from backend.app.charting.registry import CapabilityDefinition, get_capability
from backend.app.charting.schemas import ChartPlan, ChartSpec, FieldDefinition, SeriesBinding
from backend.app.charting.validators import validate_chart_plan
from backend.app.services.dataset_artifact_service import (
    DatasetArtifactError,
    get_dataset_context,
    load_dataset_artifact,
)


def _field_role(field_name: str, definition: FieldDefinition) -> str | None:
    if definition.role:
        return definition.role
    if definition.type == "datetime":
        return "time"
    if definition.type == "date":
        return "date"
    if definition.type == "category":
        return "category"
    if definition.type == "number":
        return "measure"
    return None


def _find_field(artifact, role: str) -> str | None:
    candidates = [
        name
        for name, definition in artifact.field_schema.items()
        if _field_role(name, definition) == role
    ]
    if role == "station" and not candidates:
        candidates = [
            name
            for name, definition in artifact.field_schema.items()
            if definition.type == "category" and "station" in name.lower()
        ]
    return candidates[0] if len(candidates) == 1 else None


def _measure_fields(artifact) -> list[str]:
    return [
        name
        for name, definition in artifact.field_schema.items()
        if _field_role(name, definition) == "measure"
    ]


def _group_field(artifact) -> str | None:
    """Infer the semantic series dimension declared by the dataset tool."""

    dimension = artifact.metadata.get("series_dimension")
    if isinstance(dimension, str) and dimension in artifact.field_schema:
        return dimension
    if "series_key" in artifact.field_schema:
        return "series_key"
    return _find_field(artifact, "category")


def resolve_chart_plan(plan: ChartPlan, artifact, capability: CapabilityDefinition) -> ChartPlan:
    """Fill only registry-safe defaults; explicit invalid values remain errors."""
    period_start = artifact.metadata.get("period_start")
    period_end = artifact.metadata.get("period_end")
    station_count = artifact.metadata.get("station_count")
    is_multi_station = isinstance(station_count, int) and station_count > 1
    is_multi_day = period_start is not None and period_end is not None and period_start != period_end

    inferred_view = plan.view
    if inferred_view is None and plan.capability_id == "period_aggregate":
        inferred_view = "date_trend" if not is_multi_station or is_multi_day else "station_snapshot"

    inferred_series_mode = plan.series_mode or capability.default_series_mode
    if (
        plan.capability_id == "period_aggregate"
        and is_multi_station
        and is_multi_day
    ):
        # A multi-station range is inherently a grouped daily trend. Infer the
        # safe mode when the Agent omits it, rather than forcing a failed
        # single-series attempt before the Validator can explain the shape.
        inferred_series_mode = plan.series_mode or "group"

    inferred_x_role = (
        "station" if inferred_view == "station_snapshot" else
        "date" if inferred_view == "date_trend" else
        capability.default_x_role
    )
    updates = {
        "chart_type": plan.chart_type or capability.default_chart_type,
        "template_id": plan.template_id or capability.default_template_id,
        "x_role": plan.x_role or inferred_x_role,
        "series_mode": inferred_series_mode,
        "view": inferred_view,
    }

    x_role = updates["x_role"]
    x_field = plan.x_field or _find_field(artifact, x_role)
    if not x_field:
        raise ChartValidationError(
            "X_FIELD_INFERENCE_FAILED",
            f"无法根据字段角色自动找到 {x_role} 横轴字段",
            details={"available_fields": list(artifact.field_schema)},
        )
    updates["x_field"] = x_field

    series = list(plan.series)
    if not series and updates["series_mode"] in {"single", "fields"}:
        measure_fields = _measure_fields(artifact)
        if capability.min_series == capability.max_series == 1:
            selected = measure_fields[:1]
        elif capability.min_series <= len(measure_fields) <= capability.max_series:
            selected = measure_fields
        else:
            selected = []
        series = [
            SeriesBinding(field=field, name=artifact.field_schema[field].label)
            for field in selected
        ]
    updates["series"] = series

    if updates["series_mode"] == "group":
        updates["group_field"] = plan.group_field or _group_field(artifact)
        updates["value_field"] = plan.value_field or (_measure_fields(artifact) or [None])[0]

    return plan.model_copy(update=updates)


class ChartService:
    def create_chart(self, plan: ChartPlan) -> ChartSpec:
        context = get_chart_context()
        try:
            # 生产请求使用通用数据制品服务，可从 MySQL 恢复；离线单元测试
            # 没有 DatasetContext 时仍允许读取进程内测试制品。
            if get_dataset_context(required=False) is not None:
                artifact = load_dataset_artifact(plan.artifact_id)
            else:
                from backend.app.charting.artifact_store import artifact_store

                artifact = artifact_store.get(
                    plan.artifact_id,
                    user_id=context.user_id,
                    session_id=context.session_id,
                )
        except DatasetArtifactError as exc:
            raise ChartValidationError(exc.code, exc.message) from exc
        capability = get_capability(plan.capability_id)
        if capability is None:
            # Reuse the validator's stable error code and allowed-capability details.
            validate_chart_plan(plan, artifact)
        resolved_plan = resolve_chart_plan(plan, artifact, capability)
        validate_chart_plan(resolved_plan, artifact)

        if resolved_plan.template_id == "single_line":
            return build_single_line(resolved_plan, artifact)
        if resolved_plan.template_id == "multi_line":
            return build_multi_line(resolved_plan, artifact)
        if resolved_plan.template_id == "aggregate_bar":
            return build_aggregate_bar(resolved_plan, artifact)

        raise ChartValidationError("TEMPLATE_NOT_IMPLEMENTED", f"模板暂未实现: {resolved_plan.template_id}")


chart_service = ChartService()
