"""Agent 实例、Bridge 与 session 级并发状态管理。"""
import asyncio
import contextvars
import logging
import os
import sys
import uuid
from typing import Dict, Optional

_SOLAR_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _SOLAR_AGENT_ROOT not in sys.path:
    sys.path.insert(0, _SOLAR_AGENT_ROOT)

from backend.Agent.agent import build_agent
from backend.tools.ask_user_tool import set_input_handler
from backend.app.services.ask_user_bridge import AskUserBridge
from backend.app.config import settings

logger = logging.getLogger(__name__)
_current_bridge_var: contextvars.ContextVar[Optional[AskUserBridge]] = contextvars.ContextVar("current_bridge", default=None)


class AgentManager:
    """按会话缓存 Agent，并隔离每个会话的交互状态。"""

    def __init__(self):
        self._agents: Dict[str, object] = {}
        self._bridges: Dict[str, AskUserBridge] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        set_input_handler(self._web_input_handler)

    def _web_input_handler(self, question: str) -> str:
        """根据当前工具线程绑定的 session 路由问题。"""
        bridge = _current_bridge_var.get()
        if bridge and bridge.is_active:
            return bridge.ask(question)
        active = [item for item in self._bridges.values() if item.is_active]
        if len(active) == 1:
            return active[0].ask(question)
        logger.warning("ask_user 无法确定所属会话，拒绝跨会话路由")
        return "用户回复: (当前没有可用的交互会话)"

    def create_session(self, user_id: Optional[int] = None) -> str:
        """创建会话并绑定用户。"""
        session_id = str(uuid.uuid4())[:8]
        self._agents[session_id] = build_agent(session_id=session_id, user_id=user_id)
        self._locks[session_id] = asyncio.Lock()
        logger.info("新建会话: %s, user_id=%s", session_id, user_id)
        return session_id

    def get_agent(self, session_id: str, user_id: Optional[int] = None):
        """获取 Agent；重启后从持久化记忆恢复。"""
        if session_id not in self._agents:
            self._agents[session_id] = build_agent(session_id=session_id, user_id=user_id)
        self._locks.setdefault(session_id, asyncio.Lock())
        return self._agents[session_id]

    def get_lock(self, session_id: str) -> asyncio.Lock:
        """返回 session 级执行锁。"""
        return self._locks.setdefault(session_id, asyncio.Lock())

    def get_or_create_bridge(self, session_id: str) -> AskUserBridge:
        """获取会话专属 Bridge。"""
        if session_id not in self._bridges:
            self._bridges[session_id] = AskUserBridge(session_id, timeout=settings.ask_user_timeout)
        return self._bridges[session_id]

    def get_bridge(self, session_id: str) -> Optional[AskUserBridge]:
        return self._bridges.get(session_id)

    def bind_bridge(self, bridge: AskUserBridge):
        """将当前 Agent 执行上下文绑定到会话 Bridge。"""
        return _current_bridge_var.set(bridge)

    def reset_bridge(self, token):
        """恢复之前的 Bridge 上下文。"""
        _current_bridge_var.reset(token)
        
    def remove_session(self, session_id: str) -> None:
        """清理会话内存状态。"""
        bridge = self._bridges.pop(session_id, None)
        if bridge:
            bridge.cancel()
        self._agents.pop(session_id, None)
        self._locks.pop(session_id, None)


agent_manager = AgentManager()
