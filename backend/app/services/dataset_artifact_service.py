"""通用数据制品服务。

数据查询、预测、天气等工具都可以把结构化结果登记为 DatasetArtifact。
图表、导出和后续分析只消费数据制品，不再互相依赖。
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import text

from backend.app.charting.artifact_store import artifact_store
from backend.app.charting.errors import ChartValidationError
from backend.app.charting.field_names import (
    FieldNameCollisionError,
    TIMESTAMP_FIELD,
    VALUE_KWH_FIELD,
    normalize_dataset_artifact,
    normalize_dataset_frame_columns,
    normalize_field_schema,
)
from backend.app.charting.schemas import DatasetArtifact, FieldDefinition
from backend.app.database import get_engine


logger = logging.getLogger(__name__)


class DatasetArtifactError(RuntimeError):
    """数据制品不可用或无法通过归属校验。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class DatasetExecutionContext:
    """一次 Agent 执行内的数据制品引用。

    只保存 ID 和轻量元数据，不把数据行重新拼进 Prompt。
    """

    session_id: str
    user_id: int
    active_dataset_artifact_id: str | None = None
    produced_artifact_ids: list[str] = field(default_factory=list)

    def register(self, artifact: DatasetArtifact) -> None:
        if artifact.artifact_id not in self.produced_artifact_ids:
            self.produced_artifact_ids.append(artifact.artifact_id)
        self.active_dataset_artifact_id = artifact.artifact_id


_current_context: contextvars.ContextVar[DatasetExecutionContext | None] = contextvars.ContextVar(
    "dataset_execution_context",
    default=None,
)


def bind_dataset_context(
    session_id: str,
    user_id: int,
    *,
    active_dataset_artifact_id: str | None = None,
):
    """绑定当前 Agent 执行的数据制品上下文。"""

    return _current_context.set(
        DatasetExecutionContext(
            session_id=session_id,
            user_id=user_id,
            active_dataset_artifact_id=active_dataset_artifact_id,
        )
    )


def reset_dataset_context(token) -> None:
    """恢复调用前的 ContextVar，避免不同会话之间串数据。"""

    _current_context.reset(token)


def get_dataset_context(*, required: bool = True) -> DatasetExecutionContext | None:
    context = _current_context.get()
    if context is None and required:
        raise DatasetArtifactError(
            "DATASET_CONTEXT_REQUIRED",
            "当前任务缺少数据制品执行上下文",
        )
    return context


def _json_safe(value: Any) -> Any:
    """把 pandas/numpy/datetime 值转换为可写入 MySQL JSON 的值。"""

    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, (pd.Timestamp, datetime)) else value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return value


def _infer_field_schema(frame: pd.DataFrame) -> dict[str, FieldDefinition]:
    """为通用表格推断字段角色，业务工具可通过 metadata 再补充语义。"""

    schema: dict[str, FieldDefinition] = {}
    for column in frame.columns:
        name = str(column)
        lowered = name.lower()
        if lowered == TIMESTAMP_FIELD:
            definition = FieldDefinition(type="datetime", role="time", label=name)
        elif lowered in {"date", "day"}:
            definition = FieldDefinition(type="date", role="date", label=name)
        elif pd.api.types.is_numeric_dtype(frame[column]):
            definition = FieldDefinition(type="number", role="measure", label=name)
        else:
            definition = FieldDefinition(type="category", role="category", label=name)
        schema[name] = definition
    return schema


def create_dataset_artifact(
    frame: pd.DataFrame,
    *,
    artifact_type: str,
    source_tool: str,
    metadata: dict[str, Any] | None = None,
    field_schema: dict[str, FieldDefinition] | None = None,
    artifact_id: str | None = None,
) -> DatasetArtifact:
    """把 DataFrame 标准化并登记为当前任务的数据制品。"""

    # 拿到上下文中的数据制品归属信息，保证不会被其他会话或用户访问。
    context = get_dataset_context()
    # 画图类型判断
    if artifact_type not in {"hourly_series", "daily_aggregate", "tabular"}:
        raise DatasetArtifactError("DATASET_TYPE_UNSUPPORTED", f"不支持的数据制品类型: {artifact_type}")
    if not isinstance(frame, pd.DataFrame):
        raise DatasetArtifactError("DATASET_INVALID", "数据制品必须来自 pandas.DataFrame")

    try:
        # 原始数据库/模型字段只在进入数据制品这一刻转换，避免污染底层
        # 查询和预测代码；从这里开始，图表和导出只看到标准字段名。
        safe_frame = normalize_dataset_frame_columns(frame)
        safe_frame.columns = [str(column) for column in safe_frame.columns]
        normalized_schema = normalize_field_schema(
            field_schema or _infer_field_schema(safe_frame)
        )
    except FieldNameCollisionError as exc:
        raise DatasetArtifactError("DATASET_FIELD_COLLISION", str(exc)) from exc

    rows = [
        {str(key): _json_safe(value) for key, value in row.items()}
        for row in safe_frame.to_dict(orient="records")
    ]
    artifact = DatasetArtifact(
        artifact_id=artifact_id or f"artifact_{uuid.uuid4().hex[:12]}",
        artifact_type=artifact_type,
        owner_user_id=context.user_id,
        session_id=context.session_id,
        schema=normalized_schema,
        rows=rows,
        metadata={
            **(metadata or {}),
            "source_tool": source_tool,
            "row_count": len(rows),
        },
    )
    save_dataset_artifact(artifact)
    return artifact


def dataset_reference(artifact: DatasetArtifact) -> dict[str, Any]:
    """生成可安全返回给 Agent/前端的轻量引用。"""

    metadata = artifact.metadata or {}
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type,
        "source_tool": metadata.get("source_tool"),
        "data_type": metadata.get("data_type"),
        "stations": metadata.get("stations") or ([metadata.get("station")] if metadata.get("station") else []),
        "period_start": metadata.get("period_start"),
        "period_end": metadata.get("period_end"),
        "granularity": metadata.get("granularity"),
        "row_count": len(artifact.rows),
    }


def save_dataset_artifact(artifact: DatasetArtifact) -> DatasetArtifact:
    """同时写入当前进程缓存和 MySQL，保证跨轮对话可恢复。"""

    context = get_dataset_context()
    if artifact.owner_user_id != context.user_id or artifact.session_id != context.session_id:
        raise DatasetArtifactError("DATASET_CONTEXT_MISMATCH", "数据制品与当前用户或会话不匹配")

    artifact_store.save(artifact)
    payload = {
        "schema": {name: definition.model_dump() for name, definition in artifact.field_schema.items()},
        "rows": artifact.rows,
        "metadata": artifact.metadata,
    }
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dataset_artifact_store "
                    "(artifact_id, user_id, session_id, artifact_type, source_tool, "
                    "schema_json, rows_json, metadata_json) "
                    "VALUES (:aid, :uid, :sid, :atype, :tool, :schema, :rows, :metadata) "
                    "ON DUPLICATE KEY UPDATE schema_json = VALUES(schema_json), "
                    "rows_json = VALUES(rows_json), metadata_json = VALUES(metadata_json)"
                ),
                {
                    "aid": artifact.artifact_id,
                    "uid": artifact.owner_user_id,
                    "sid": artifact.session_id,
                    "atype": artifact.artifact_type,
                    "tool": artifact.metadata.get("source_tool"),
                    "schema": json.dumps(payload["schema"], ensure_ascii=False),
                    "rows": json.dumps(payload["rows"], ensure_ascii=False),
                    "metadata": json.dumps(payload["metadata"], ensure_ascii=False),
                },
            )
    except Exception as exc:
        logger.exception("持久化数据制品失败")
        raise DatasetArtifactError(
            "DATASET_PERSISTENCE_FAILED",
            "数据制品持久化失败，请确认已执行 012_dataset_artifact_store.sql",
        ) from exc
    context.register(artifact)
    return artifact


# 读取数据制品，保证 Agent 只能访问当前会话和用户的制品。
def _load_persisted_artifact(artifact_id: str, *, user_id: int, session_id: str) -> DatasetArtifact:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT artifact_type, schema_json, rows_json, metadata_json "
                "FROM dataset_artifact_store "
                "WHERE artifact_id = :aid AND user_id = :uid AND session_id = :sid"
            ),
            {"aid": artifact_id, "uid": user_id, "sid": session_id},
        ).fetchone()
    if row is None:
        raise DatasetArtifactError("DATASET_NOT_FOUND", "数据制品不存在或不属于当前会话")
    try:
        schema_payload = json.loads(row[1])
        rows = json.loads(row[2])
        metadata = json.loads(row[3])
        artifact = DatasetArtifact(
            artifact_id=artifact_id,
            artifact_type=row[0],
            owner_user_id=user_id,
            session_id=session_id,
            schema={name: FieldDefinition(**definition) for name, definition in schema_payload.items()},
            rows=rows,
            metadata=metadata,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DatasetArtifactError("DATASET_CORRUPTED", "数据制品内容无法恢复") from exc
    artifact_store.save(artifact)
    return artifact


def load_dataset_artifact(artifact_id: str) -> DatasetArtifact:
    """按当前用户和会话读取数据制品，优先进程缓存，失败后查 MySQL。"""

    context = get_dataset_context()
    # 从进程缓存中读取，避免每次都访问 MySQL。
    try:
        artifact = artifact_store.get(
            artifact_id,
            user_id=context.user_id,
            session_id=context.session_id,
        )
        return normalize_dataset_artifact(artifact)
    # 
    except ChartValidationError:
        artifact = _load_persisted_artifact(
            artifact_id,
            user_id=context.user_id,
            session_id=context.session_id,
        )
        return normalize_dataset_artifact(artifact)


def resolve_dataset_for_export(artifact_id: str = "") -> DatasetArtifact:
    """解析导出来源：当前任务制品优先，其次才使用会话最近制品。"""

    context = get_dataset_context()
    requested_id = str(artifact_id or "").strip()
    selected_id = requested_id or context.active_dataset_artifact_id
    if not selected_id:
        raise DatasetArtifactError(
            "EXPORT_DATASET_REQUIRED",
            "当前没有可导出的数据制品，请先查询或预测数据",
        )
    if requested_id and context.produced_artifact_ids and requested_id not in context.produced_artifact_ids:
        # 图表快照中的旧引用不会进入 produced_artifact_ids，因此不会被当成当前数据导出。
        raise DatasetArtifactError(
            "EXPORT_DATASET_NOT_CURRENT",
            "指定的数据制品不是当前任务或会话绑定的数据，请先重新查询目标数据",
        )
    return load_dataset_artifact(selected_id)


def make_power_frame(
    frame: pd.DataFrame,
    *,
    station_name: str,
    source_type: str,
    period_start: str,
    period_end: str,
    timestamp_column: str,
    value_column: str,
    source_tool: str,
    station_id: int | str | None = None,
    weather_type: str | None = None,
) -> pd.DataFrame:
    """将实际/预测原始表转换为统一的电量长表。"""

    result = pd.DataFrame(
        {
            TIMESTAMP_FIELD: pd.to_datetime(frame[timestamp_column]),
            "station": station_name,
            "source_type": source_type,
            "series_key": source_type,
            VALUE_KWH_FIELD: pd.to_numeric(frame[value_column], errors="coerce").fillna(0.0),
        }
    )
    result[TIMESTAMP_FIELD] = result[TIMESTAMP_FIELD].dt.strftime("%Y-%m-%d %H:%M:%S")
    result.attrs.update(
        {
            "artifact_type": "hourly_series",
            "source_tool": source_tool,
            "station_id": station_id,
            "station": station_name,
            "stations": [station_name],
            "source_type": source_type,
            "data_type": source_type,
            "period_start": period_start,
            "period_end": period_end,
            "granularity": "hourly",
            "series_dimension": "source_type",
            "weather_type": weather_type,
        }
    )
    return result


def register_power_frame(frame: pd.DataFrame) -> DatasetArtifact:
    """登记 make_power_frame 生成的统一电量表。"""

    attrs = dict(frame.attrs)
    schema = {
        TIMESTAMP_FIELD: FieldDefinition(type="datetime", role="time", label="时间"),
        "station": FieldDefinition(type="category", role="category", label="站点"),
        "source_type": FieldDefinition(type="category", role="category", label="数据来源"),
        "series_key": FieldDefinition(type="category", role="category", label="图表序列"),
        VALUE_KWH_FIELD: FieldDefinition(type="number", role="measure", label="发电量", unit="kWh"),
    }
    return create_dataset_artifact(
        frame,
        artifact_type=attrs.get("artifact_type", "hourly_series"),
        source_tool=attrs.get("source_tool", "power_query"),
        field_schema=schema,
        metadata={key: value for key, value in attrs.items() if key != "artifact_type" and value is not None},
    )
