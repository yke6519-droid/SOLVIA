"""Background scheduling for per-session conversation summaries."""

import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class SummaryTaskManager:
    """Schedule at most one summary refresh at a time for each session."""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def schedule(self, session_id: str, memory) -> bool:
        """Queue a summary refresh without blocking the current request."""
        existing = self._tasks.get(session_id)
        if existing and not existing.done():
            logger.debug("摘要任务已在运行，跳过重复调度: session=%s", session_id)
            return False

        session_lock = self._locks.setdefault(session_id, asyncio.Lock())

        async def run() -> None:
            try:
                async with session_lock:
                    await memory.amaybe_summarize()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("后台摘要生成失败: session=%s", session_id)
            finally:
                current = asyncio.current_task()
                if self._tasks.get(session_id) is current:
                    self._tasks.pop(session_id, None)

        task = asyncio.create_task(run(), name=f"summary:{session_id}")
        self._tasks[session_id] = task
        logger.debug("已调度后台摘要任务: session=%s", session_id)
        return True

    async def shutdown(self) -> None:
        """Cancel outstanding summaries during application shutdown."""
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._locks.clear()


summary_task_manager = SummaryTaskManager()
