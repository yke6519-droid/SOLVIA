"""Persist structured chart snapshots for historical conversations."""

import json
import logging
from typing import Iterable, List

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.database import get_engine

logger = logging.getLogger(__name__)


def _get_engine():
    try:
        return get_engine()
    except RuntimeError:
        return None


def _latest_assistant_message_id(conn, session_id: str, user_id: int):
    rows = conn.execute(text(
        "SELECT id, message FROM message_store "
        "WHERE session_id = :sid AND user_id = :uid "
        "ORDER BY id DESC LIMIT 30"
    ), {"sid": session_id, "uid": user_id}).fetchall()
    for row in rows:
        try:
            payload = json.loads(row[1])
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("type") in {"ai", "assistant"}:
            return int(row[0])
    return None


def save_chart_snapshot(session_id: str, user_id: int, charts: Iterable[dict]) -> None:
    """Save the chart specs generated in one assistant turn."""
    chart_list = [item for item in charts if isinstance(item, dict)]
    if not chart_list:
        return
    engine = _get_engine()
    if engine is None:
        logger.warning("MYSQL_URL 未配置，跳过图表历史快照保存")
        return
    try:
        with engine.begin() as conn:
            message_id = _latest_assistant_message_id(conn, session_id, user_id)
            conn.execute(text(
                "INSERT INTO chart_snapshot_store "
                "(session_id, user_id, message_id, chart_data) "
                "VALUES (:sid, :uid, :message_id, :chart_data)"
            ), {
                "sid": session_id,
                "uid": user_id,
                "message_id": message_id,
                "chart_data": json.dumps(chart_list, ensure_ascii=False),
            })
    except SQLAlchemyError:
        logger.exception("图表历史快照保存失败，实时对话不受影响")


def load_latest_chart_for_reference(
    session_id: str,
    user_id: int,
    *,
    chart_id: str | None = None,
    artifact_id: str | None = None,
) -> dict | None:
    """Find the latest chart spec belonging to a session.

    One snapshot row may contain multiple chart specs from one assistant turn.
    The SQL query is ownership-scoped before JSON values are inspected.
    """
    engine = _get_engine()
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT chart_data FROM chart_snapshot_store "
                "WHERE session_id = :sid AND user_id = :uid "
                "ORDER BY id DESC LIMIT 100"
            ), {"sid": session_id, "uid": user_id}).fetchall()
        for row in rows:
            try:
                charts = json.loads(row[0])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(charts, dict):
                charts = [charts]
            if not isinstance(charts, list):
                continue
            for chart in charts:
                if not isinstance(chart, dict):
                    continue
                metadata = chart.get("metadata")
                metadata = metadata if isinstance(metadata, dict) else {}
                if chart_id and chart.get("chart_id") == chart_id:
                    return chart
                if artifact_id and metadata.get("artifact_id") == artifact_id:
                    return chart
        return None
    except SQLAlchemyError:
        logger.warning("查询图表快照失败", exc_info=True)
        return None


def load_chart_snapshots(
    session_id: str,
    user_id: int,
    message_ids: Iterable[int] | None = None,
) -> List[dict]:
    """Load snapshots for the history endpoint; missing table is non-fatal."""
    engine = _get_engine()
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            params = {"sid": session_id, "uid": user_id}
            snapshot_filter = ""
            selected_ids = [int(item) for item in (message_ids or [])]
            if selected_ids:
                placeholders = ", ".join(
                    f":message_id_{index}" for index in range(len(selected_ids))
                )
                params.update({
                    f"message_id_{index}": message_id
                    for index, message_id in enumerate(selected_ids)
                })
                snapshot_filter = f" AND (message_id IN ({placeholders}) OR message_id IS NULL)"
            rows = conn.execute(text(
                "SELECT message_id, chart_data, created_at "
                "FROM chart_snapshot_store "
                "WHERE session_id = :sid AND user_id = :uid"
                f"{snapshot_filter} ORDER BY created_at, id"
            ), params).fetchall()
        snapshots = []
        for row in rows:
            try:
                charts = json.loads(row[1])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(charts, dict):
                charts = [charts]
            if not isinstance(charts, list):
                continue
            snapshots.append({
                "message_id": int(row[0]) if row[0] is not None else None,
                "charts": [item for item in charts if isinstance(item, dict)],
                "created_at": str(row[2]) if row[2] is not None else None,
            })
        return snapshots
    except SQLAlchemyError:
        logger.warning("图表历史表不存在或读取失败，请执行图表快照迁移", exc_info=True)
        return []


def delete_chart_snapshots(session_id: str, user_id: int) -> None:
    engine = _get_engine()
    if engine is None:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM chart_snapshot_store WHERE session_id = :sid AND user_id = :uid"
            ), {"sid": session_id, "uid": user_id})
    except SQLAlchemyError:
        logger.warning("删除图表历史快照失败", exc_info=True)
