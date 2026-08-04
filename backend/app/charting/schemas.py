"""Pydantic contracts for the first structured chart capability."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["datetime", "date", "category", "number"]
    role: Literal["time", "date", "category", "measure"] | None = None
    label: str
    unit: str | None = None


class ChartRequest(BaseModel):
    """图表请求协议：只描述用户想看的内容，不暴露底层字段绑定。"""

    model_config = ConfigDict(extra="forbid")

    station_names: list[str] = Field(min_length=1)
    target_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    source_types: list[Literal["actual", "predicted"]] = Field(
        default_factory=lambda: ["actual"]
    )
    granularity: Literal["hourly", "daily_total"] = "hourly"
    title: str | None = Field(default=None, max_length=80)


class DatasetArtifact(BaseModel):
    """A business tool's normalized, permission-scoped data result."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    artifact_id: str
    artifact_type: Literal["hourly_series", "daily_aggregate", "tabular"]
    owner_user_id: int
    session_id: str
    field_schema: dict[str, FieldDefinition] = Field(alias="schema")
    rows: list[dict[str, Any]]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChartDataView(BaseModel):
    """图表服务消费的内部数据视图，不作为 Agent 输入协议。"""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    field_schema: dict[str, FieldDefinition]
    rows: list[dict[str, Any]]
    x_field: str
    x_role: Literal["time", "date", "station"]
    value_field: str
    series_field: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SeriesBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    name: str | None = None


class ChartPlan(BaseModel):
    """Agent-submitted plan; it contains references, never raw chart data."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    capability_id: str
    chart_type: str | None = None
    template_id: str | None = None
    artifact_id: str
    x_field: str | None = None
    x_role: Literal["time", "date", "station"] | None = None
    series_mode: Literal["single", "fields", "group"] | None = None
    series: list[SeriesBinding] = Field(default_factory=list)
    group_field: str | None = None
    value_field: str | None = None
    view: Literal["date_trend", "station_snapshot"] | None = None
    title: str | None = Field(default=None, max_length=80)


class AxisSpec(BaseModel):
    role: Literal["time", "date", "station"]
    label: str
    data: list[str]


class YAxisSpec(BaseModel):
    label: str
    unit: str | None = None


class ChartSeries(BaseModel):
    name: str
    data: list[float | None]
    unit: str | None = None
    color: str | None = None


class ChartSpec(BaseModel):
    """Backend-generated contract consumed by SSE, snapshots and Vue."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    chart_id: str
    capability_id: str
    template_id: str
    chart_type: Literal["line", "bar"]
    title: str
    x_axis: AxisSpec
    y_axis: YAxisSpec
    series: list[ChartSeries]
    metadata: dict[str, Any] = Field(default_factory=dict)
