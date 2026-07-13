"""
agent_manager.py - Agent 实例与 Bridge 管理器
=============================================

职责:
  1. 按 session_id 缓存 AgentExecutor（避免每条消息重建 LLM/工具）
  2. 管理 AskUserBridge（每个 session 一个）
  3. 设置 ask_user 的 Web handler（替代 CLI 的 input()）

对应 Spring 概念:
  - AgentManager ≈ 一个 @Service 单例，维护 Map<sessionId, AgentExecutor>
  - set_input_handler ≈ 策略模式注入，把 CLI 的 Scanner/input 换成 Web 回调

⚠️ 当前限制:
  _current_bridge 是单值，同一时刻只支持一个 ask_user 交互。
  对于单用户演示完全够用；多并发 session 需要 contextvars 方案。
"""
import sys
import os
import uuid
import logging
from typing import Dict, Optional

# 确保 solar_agent 根目录在 sys.path 中
_SOLAR_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SOLAR_AGENT_ROOT not in sys.path:
    sys.path.insert(0, _SOLAR_AGENT_ROOT)

from Agent.agent import build_agent
from predModels.Tools.ask_user_tool import set_input_handler
from app.services.ask_user_bridge import AskUserBridge
from app.config import settings

logger = logging.getLogger(__name__)


class AgentManager:
    """Agent 实例缓存 + AskUserBridge 管理。"""

    def __init__(self):
        self._agents: Dict[str, object] = {}        # session_id -> AgentExecutor
        self._bridges: Dict[str, AskUserBridge] = {} # session_id -> Bridge
        self._current_bridge: Optional[AskUserBridge] = None

        # 一次性替换 ask_user 的全局 handler
        # 之后所有 ask_user 调用都会走 _web_input_handler → current_bridge.ask()
        set_input_handler(self._web_input_handler)
        logger.info("[AgentManager] ask_user handler 已替换为 Web 模式")

    def _web_input_handler(self, question: str) -> str:
        """
        全局 ask_user handler，替代 CLI 的 input()。
        路由到当前活跃的 bridge。
        """
        if self._current_bridge and self._current_bridge.is_active:
            return self._current_bridge.ask(question)
        logger.warning("[AgentManager] ask_user 被调用但无活跃 bridge，返回空")
        return ""

    def create_session(self) -> str:
        """创建新会话，返回 session_id。"""
        session_id = str(uuid.uuid4())[:8]
        # 预构建 AgentExecutor 并缓存
        executor = build_agent(session_id=session_id, use_db=True)
        self._agents[session_id] = executor
        logger.info(f"[AgentManager] 新建会话: {session_id}")
        return session_id

    def get_agent(self, session_id: str):
        """获取或创建 AgentExecutor。"""
        if session_id not in self._agents:
            executor = build_agent(session_id=session_id, use_db=True)
            self._agents[session_id] = executor
        return self._agents[session_id]

    def get_or_create_bridge(self, session_id: str) -> AskUserBridge:
        """获取或创建 Bridge。"""
        if session_id not in self._bridges:
            self._bridges[session_id] = AskUserBridge(
                session_id, timeout=settings.ask_user_timeout
            )
        return self._bridges[session_id]

    def set_current_bridge(self, bridge: Optional[AskUserBridge]):
        """设置当前活跃 bridge（SSE 请求开始时调用，结束时置 None）。"""
        self._current_bridge = bridge

    def get_bridge(self, session_id: str) -> Optional[AskUserBridge]:
        return self._bridges.get(session_id)


# 全局单例
agent_manager = AgentManager()
