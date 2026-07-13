"""
ask_user_bridge.py - Web 模式 ask_user 桥接器
=============================================

核心问题:
  ask_user 工具是同步的（在 LangChain 线程池中运行），
  需要 input() 阻塞等待用户输入。
  但 SSE 流在 asyncio 事件循环中运行，不能阻塞。

解决思路（类比 Spring）:
  ┌──────────────────────────────────────────────────────┐
  │  Worker Thread (ask_user 工具)                        │
  │    bridge.ask(question)                               │
  │      ├─ call_soon_threadsafe → 把问题推入 Queue       │
  │      └─ threading.Event.wait()  ← 阻塞当前线程        │
  │                                                        │
  │  Event Loop (SSE 生成器)                              │
  │    queue.get() → yield user_input_required 事件       │
  │    ... 等待用户回复 ...                                │
  │                                                        │
  │  HTTP Thread (POST /reply)                            │
  │    bridge.reply(answer) → Event.set() → 解除阻塞      │
  └──────────────────────────────────────────────────────┘

  对应 Spring 概念:
    - threading.Event ≈ CountDownLatch(1) / CompletableFuture
    - asyncio.Queue ≈ BlockingQueue / SseEmitter
    - call_soon_threadsafe ≈ 跨线程提交任务到主线程
"""
import threading
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AskUserBridge:
    """
    桥接 ask_user 工具（同步线程）与 SSE 流（异步事件循环）。

    生命周期:
      1. SSE 请求开始 → attach(queue, loop) 绑定队列和事件循环
      2. Agent 调用 ask_user → ask(question) 阻塞线程，推送问题到队列
      3. SSE 生成器从队列取出问题 → 推送 user_input_required 事件给前端
      4. 前端发 POST /reply → reply(answer) 解除阻塞
      5. SSE 请求结束 → detach() 解绑
    """

    def __init__(self, session_id: str, timeout: int = 120):
        self.session_id = session_id
        self.timeout = timeout

        # 线程间同步：阻塞 ask_user 工具线程，直到用户回复
        self._reply_event = threading.Event()
        self._reply: Optional[str] = None

        # 与 SSE 生成器的通信通道
        self._queue: Optional[asyncio.Queue] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def attach(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        """SSE 生成器调用：绑定队列和事件循环。"""
        self._queue = queue
        self._loop = loop

    def detach(self):
        """SSE 生成器结束时调用：解绑。"""
        self._queue = None
        self._loop = None
        self._reply = None
        self._reply_event.clear()

    def ask(self, question: str) -> str:
        """
        ask_user 工具调用（在 LangChain 线程池的 Worker 线程中执行）。

        1. 把问题通过 call_soon_threadsafe 推入 asyncio.Queue
        2. 阻塞当前线程等待回复
        3. reply() 被调用后解除阻塞，返回用户回复
        """
        self._reply = None
        self._reply_event.clear()

        if self._loop and self._queue:
            # 跨线程安全地把问题推入事件循环的队列
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait,
                ("question", {"question": question})
            )
            logger.info(f"[Bridge] 问题已推送到SSE: {question[:50]}...")

        # 阻塞 Worker 线程，等待用户回复（最长 timeout 秒）
        # 注意：这只阻塞线程池中的一个线程，不影响事件循环
        if self._reply_event.wait(timeout=self.timeout):
            logger.info(f"[Bridge] 收到用户回复: {self._reply}")
            return f"用户回复: {self._reply or ''}"
        else:
            logger.warning(f"[Bridge] 等待回复超时({self.timeout}s)")
            return "用户回复: (超时未回复，请重新提问)"

    def reply(self, answer: str):
        """
        POST /chat/{id}/reply 端点调用（在 HTTP 请求线程中执行）。
        解除 ask() 的阻塞。
        """
        self._reply = answer
        self._reply_event.set()

    @property
    def is_active(self) -> bool:
        """是否已绑定到某个 SSE 生成器。"""
        return self._queue is not None
