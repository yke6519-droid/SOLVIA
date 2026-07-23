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
