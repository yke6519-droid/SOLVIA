"""Runtime chart capability allow-list.

Only capabilities in this registry with ``enabled=True`` can be used by the
Agent. Future candidates deliberately live outside the runtime registry.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: str
    enabled: bool
    chart_types: tuple[str, ...]
    x_roles: tuple[str, ...]
    series_modes: tuple[str, ...]
    min_series: int
    max_series: int
    max_points_per_series: int
    max_total_points: int
    allowed_granularities: tuple[str, ...]
    templates: tuple[str, ...]
    default_chart_type: str
    default_template_id: str
    default_x_role: str
    default_series_mode: str
    allowed_views: tuple[str, ...] = ()


CAPABILITY_REGISTRY: dict[str, CapabilityDefinition] = {
    "time_series_trend": CapabilityDefinition(
        capability_id="time_series_trend",
        enabled=True,
        chart_types=("line",),
        x_roles=("time",),
        series_modes=("single",),
        min_series=1,
        max_series=1,
        max_points_per_series=744,
        max_total_points=744,
        allowed_granularities=("hourly",),
        templates=("single_line",),
        default_chart_type="line",
        default_template_id="single_line",
        default_x_role="time",
        default_series_mode="single",
    ),
    "time_series_compare": CapabilityDefinition(
        capability_id="time_series_compare",
        enabled=True,
        chart_types=("line",),
        x_roles=("time",),
        # The first implementation uses long-form rows and one semantic group
        # field (source_type or station). Wide-field bindings can be registered
        # later once a separate builder is available.
        series_modes=("group",),
        min_series=2,
        max_series=4,
        max_points_per_series=744,
        max_total_points=2976,
        allowed_granularities=("hourly",),
        templates=("multi_line",),
        default_chart_type="line",
        default_template_id="multi_line",
        default_x_role="time",
        # Long-form artifacts use one row per (time, series), so grouping is
        # the generic mode for both source and station comparisons.
        default_series_mode="group",
    ),
    "period_aggregate": CapabilityDefinition(
        capability_id="period_aggregate",
        enabled=True,
        chart_types=("bar",),
        x_roles=("date", "station"),
        # single covers one station's daily trend; group covers dynamic
        # multi-station daily totals without hard-coding a station count.
        series_modes=("single", "group"),
        min_series=1,
        max_series=4,
        max_points_per_series=50,
        max_total_points=200,
        allowed_granularities=("daily_total",),
        templates=("aggregate_bar",),
        default_chart_type="bar",
        default_template_id="aggregate_bar",
        default_x_role="date",
        default_series_mode="single",
        allowed_views=("date_trend", "station_snapshot"),
    ),
}


# Candidate definitions are intentionally not exposed to the runtime registry.
CANDIDATE_CAPABILITIES = {
    "category_compare": {"enabled": False},
    "proportion_distribution": {"enabled": False},
}


def get_capability(capability_id: str) -> CapabilityDefinition | None:
    definition = CAPABILITY_REGISTRY.get(capability_id)
    if definition is None or not definition.enabled:
        return None
    return definition


def get_enabled_capabilities() -> list[dict]:
    return [
        {
            "capability_id": item.capability_id,
            "chart_types": list(item.chart_types),
            "x_roles": list(item.x_roles),
            "series_modes": list(item.series_modes),
            "min_series": item.min_series,
            "max_series": item.max_series,
            "templates": list(item.templates),
            "allowed_granularities": list(item.allowed_granularities),
            "max_points_per_series": item.max_points_per_series,
            "max_total_points": item.max_total_points,
            "allowed_views": list(item.allowed_views),
            "defaults": {
                "chart_type": item.default_chart_type,
                "template_id": item.default_template_id,
                "x_role": item.default_x_role,
                "series_mode": item.default_series_mode,
            },
        }
        for item in CAPABILITY_REGISTRY.values()
        if item.enabled
    ]
