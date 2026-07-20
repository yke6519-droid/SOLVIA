"""Persist structured chart snapshots separately from LangChain's internal message history."""
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


# 获取最新一条消息的message_id
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


# 将图表快照数据入库
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
            # 拿到当前对话的message_id，作为图表快照的关联
            message_id = _latest_assistant_message_id(conn, session_id, user_id)
            # 插入图表快照
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


# 加载图表快照数据返回给前端渲染
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
                snapshot_filter = (
                    f" AND (message_id IN ({placeholders}) OR message_id IS NULL)"
                )
            rows = conn.execute(text(
                "SELECT message_id, chart_data, created_at "
                "FROM chart_snapshot_store "
                "WHERE session_id = :sid AND user_id = :uid"
                f"{snapshot_filter} ORDER BY created_at, id"
            ), params).fetchall()
        # 解析每一行的 chart_data JSON，返回一个列表，每个元素包含 message_id、charts 列表和 created_at
        snapshots = []
        for row in rows:
            try:
                # chart_data 可能是单个对象或列表，统一转换为列表
                charts = json.loads(row[1])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(charts, dict):
                # 如果是单个对象，包装成列表
                charts = [charts]
            if not isinstance(charts, list):
                continue
            # 过滤掉非字典的元素，确保 charts 列表只包含字典
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
        logger.warning("图表历史快照删除失败", exc_info=True)
