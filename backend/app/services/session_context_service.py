"""Small, durable conversation context stored with ``chat_session``.

Only compact business references belong here.  Large chat messages, chart
artifacts, and prediction arrays remain in their existing stores.  The first
version persists the last explicitly confirmed station so pronouns such as
``它`` can be resolved in the next user turn.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.app.database import get_engine


def _decode_context(raw: Any) -> dict[str, Any]:
    """Decode MySQL JSON/text values while tolerating an empty old row."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(value) if isinstance(value, dict) else {}
    return {}


def load_context(session_id: str, user_id: int) -> dict[str, Any]:
    """Load compact context for a session owned by the current user."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT context_json FROM chat_session "
                "WHERE session_id = :sid AND user_id = :uid"
            ),
            {"sid": session_id, "uid": user_id},
        ).fetchone()
    return _decode_context(row[0]) if row else {}


def save_active_station(
    session_id: str,
    user_id: int,
    station: dict[str, Any],
) -> None:
    """Persist only the fields needed to reuse a confirmed station safely."""
    compact_station = {
        key: station.get(key)
        for key in (
            "station_id",
            "name",
            "province",
            "city",
            "location",
            "lat",
            "lon",
            "capacity_kw",
        )
        if station.get(key) is not None
    }
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT context_json FROM chat_session "
                "WHERE session_id = :sid AND user_id = :uid FOR UPDATE"
            ),
            {"sid": session_id, "uid": user_id},
        ).fetchone()
        if row is None:
            return
        context = _decode_context(row[0])
        context["active_station"] = compact_station
        context["last_scope"] = {"type": "single"}
        conn.execute(
            text(
                "UPDATE chat_session SET context_json = :context_json "
                "WHERE session_id = :sid AND user_id = :uid"
            ),
            {
                "sid": session_id,
                "uid": user_id,
                "context_json": json.dumps(context, ensure_ascii=False),
            },
        )


def clear_active_station(session_id: str, user_id: int) -> None:
    """Clear a stale active station without deleting other context keys."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT context_json FROM chat_session "
                "WHERE session_id = :sid AND user_id = :uid FOR UPDATE"
            ),
            {"sid": session_id, "uid": user_id},
        ).fetchone()
        if row is None:
            return
        context = _decode_context(row[0])
        context.pop("active_station", None)
        conn.execute(
            text(
                "UPDATE chat_session SET context_json = :context_json "
                "WHERE session_id = :sid AND user_id = :uid"
            ),
            {
                "sid": session_id,
                "uid": user_id,
                "context_json": json.dumps(context, ensure_ascii=False),
            },
        )


def save_active_attachment(
    session_id: str,
    user_id: int,
    attachment: dict[str, Any],
) -> None:
    """保存当前会话最近上传的附件引用，不保存真实路径。"""
    compact_attachment = {
        "attachment_id": attachment.get("attachment_id"),
        "filename": attachment.get("filename"),
        "content_type": attachment.get("content_type"),
        "size_bytes": attachment.get("size_bytes"),
        "status": attachment.get("status", "uploaded"),
    }
    if not compact_attachment["attachment_id"]:
        return
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT context_json FROM chat_session "
                "WHERE session_id = :sid AND user_id = :uid FOR UPDATE"
            ),
            {"sid": session_id, "uid": user_id},
        ).fetchone()
        if row is None:
            return
        context = _decode_context(row[0])
        # 存入上下文中
        context["active_attachment"] = compact_attachment
        conn.execute(
            text(
                "UPDATE chat_session SET context_json = :context_json "
                "WHERE session_id = :sid AND user_id = :uid"
            ),
            {
                "sid": session_id,
                "uid": user_id,
                "context_json": json.dumps(context, ensure_ascii=False),
            },
        )


def save_active_chart(
    session_id: str,
    user_id: int,
    chart: dict[str, Any],
) -> None:
    """Persist a compact reference to the latest durable chart snapshot.

    The chart values stay in chart_snapshot_store. Session context only keeps
    identifiers and display metadata so the next user turn can export the
    previous chart without copying arrays into the prompt.
    """
    metadata = chart.get("metadata") if isinstance(chart, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    compact_chart = {
        "chart_id": chart.get("chart_id"),
        "artifact_id": metadata.get("artifact_id"),
        "title": chart.get("title"),
        "chart_type": chart.get("chart_type"),
        "capability_id": chart.get("capability_id"),
    }
    compact_chart = {
        key: value
        for key, value in compact_chart.items()
        if value not in (None, "")
    }
    if not compact_chart.get("chart_id") and not compact_chart.get("artifact_id"):
        return

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT context_json FROM chat_session "
                "WHERE session_id = :sid AND user_id = :uid FOR UPDATE"
            ),
            {"sid": session_id, "uid": user_id},
        ).fetchone()
        if row is None:
            return
        context = _decode_context(row[0])
        context["active_chart"] = compact_chart
        conn.execute(
            text(
                "UPDATE chat_session SET context_json = :context_json "
                "WHERE session_id = :sid AND user_id = :uid"
            ),
            {
                "sid": session_id,
                "uid": user_id,
                "context_json": json.dumps(context, ensure_ascii=False),
            },
        )
