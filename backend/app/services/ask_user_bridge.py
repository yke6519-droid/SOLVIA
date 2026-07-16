"""同步 ask_user 工具与异步 SSE 的桥接器。"""
import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class AskUserBridge:
    """每个 session 独立的人工确认桥接器。"""

    def __init__(self, session_id: str, timeout: int = 60):
        self.session_id = session_id
        self.timeout = timeout
        self._reply_event = threading.Event()
        self._reply: Optional[str] = None
        self._queue: Optional[asyncio.Queue] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._waiting = False
    
    def attach(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self._queue = queue
        self._loop = loop
    
    
    def detach(self):
        self._queue = None
        self._loop = None
        self._waiting = False
        self._reply = None
        self._reply_event.clear()


    def ask(self, question: str) -> str:
        """推送真实问题并阻塞工具线程等待回复。"""
        self._reply = None
        self._waiting = True
        self._reply_event.clear()
        if self._loop and self._queue:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, ("question", {"question": question}))
        received = self._reply_event.wait(timeout=self.timeout)
        self._waiting = False
        if received:
            return f"用户回复: {self._reply or ''}"
        return "用户回复: (超时未回复，请重新提问)"


    def reply(self, answer: str):
        """设置回复并唤醒等待线程。"""
        if not self._waiting:
            return
        self._reply = answer
        self._reply_event.set()


    def cancel(self):
        """取消等待中的交互。"""
        self._reply = "(会话已取消)"
        self._waiting = False
        self._reply_event.set()


    @property
    def is_waiting(self) -> bool:
        return self._waiting


    @property
    def is_active(self) -> bool:
        return self._queue is not None and self._loop is not None