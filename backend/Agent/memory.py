"""
memory.py - Agent 多轮对话记忆模块
=================================
架构: 窗口记忆 + 异步摘要 + MySQL 持久化

设计原则:
  1. 只追加不删除 — message_store 只 INSERT, 窗口在读取时截取
  2. 零阻塞 — 摘要异步生成, 不阻塞对话主链路
  3. 持久化 — 摘要和消息都落 MySQL, 重启可恢复
  4. 用户隔离 — 每条消息携带 user_id, 可按用户查询会话

当前运行模式: MySQL 持久化
"""
import json
import logging
from typing import Any, Dict, Optional, List

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import BaseMessage, SystemMessage, messages_from_dict
from langchain.memory.chat_memory import BaseChatMemory
from langchain.memory.summary import SummarizerMixin

logger = logging.getLogger(__name__)

# ============================================================
# 自定义 SQL 消息转换器（带 user_id）
# ============================================================
# 用 ensure_ascii=False 存储原始中文，同时携带 user_id 实现用户隔离。

from langchain_community.chat_message_histories.sql import DefaultMessageConverter
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, Text, String, DateTime, BigInteger, func, text

from backend.app.database import get_engine


# 自定义 ORM Model，比 LangChain 默认多一个 user_id 列
ConverterBase = declarative_base()


class UserAwareMessage(ConverterBase):
    """message_store 的 ORM 映射，额外携带 user_id 列。"""
    __tablename__ = "message_store"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    user_id = Column(BigInteger, nullable=True, index=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class ChineseFriendlyConverter(DefaultMessageConverter):
    """支持中文原文存储 + user_id 的 SQL 消息转换器"""

    def __init__(self, table_name: str, user_id: Optional[int] = None):
        # 不调用 super().__init__，避免创建不含 user_id 的默认 model
        self._user_id = user_id
        self.model_class = UserAwareMessage

    def to_sql_model(self, message: BaseMessage, session_id: str) -> Any:
        from langchain_core.messages import message_to_dict
        return self.model_class(
            session_id=session_id,
            user_id=self._user_id,
            message=json.dumps(message_to_dict(message), ensure_ascii=False)
        )


# ============================================================
# 持久化窗口摘要记忆
# ============================================================

class PersistentWindowSummaryMemory(BaseChatMemory, SummarizerMixin):
    """
    窗口 + 摘要 + 持久化记忆。

    - 最近 k 轮对话保留完整原文 (窗口)
    - 更早的对话用 LLM 总结成摘要
    - 消息和摘要都持久化到 MySQL, 重启可恢复
    - 摘要异步触发, 不阻塞对话
    - 每条消息携带 user_id, 实现用户隔离
    """

    k: int = 3
    memory_key: str = "chat_history"
    summary_trigger_messages: int = 8
    summary_batch_size: int = 16
    session_id: str = "default"
    user_id: Optional[int] = None  # 消息所有者ID,用于用户隔离

    @property
    def memory_variables(self) -> List[str]:
        return [self.memory_key]

    # ============================================================
    # 读取: 每轮对话前触发
    # ============================================================
    def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        读取记忆: 摘要 + 最近 k 轮窗口消息。

        流程:
          1. 从 message_store 直接读取最近 2*k 条消息 (1 次 SELECT)
          2. 从 agent_summary_store 读取摘要 (1 次 SELECT)
          3. 按时间正序恢复窗口消息
          4. 拼装: 摘要在前 + 窗口消息在后
        """
        # 1. 在数据库模式下直接读取最近窗口，避免先加载整个会话。
        window_size = 2 * self.k
        # 新增函数，进读取最近窗口内的记忆
        window = self._load_recent_messages(window_size)

        # 2. 读取摘要
        summary = self._load_summary()

        # 3. 拼装
        result: List[BaseMessage] = []
        if summary:
            result.append(SystemMessage(content=f"之前对话摘要: {summary}"))
        result.extend(window)

        return {self.memory_key: result}


    def _load_recent_messages(self, limit: int) -> List[BaseMessage]:
        """只读取最近窗口消息，并按时间正序返回。"""
        if limit <= 0:
            return []

        params = {
            "session_id": self.session_id,
            "limit": limit,
        }
        user_condition = "user_id IS NULL"

        if self.user_id is not None:
            user_condition = "user_id = :user_id"
            params["user_id"] = self.user_id

        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT message FROM message_store "
                    "WHERE session_id = :session_id "
                    f"AND {user_condition} "
                    "ORDER BY id DESC LIMIT :limit"
                ),
                params,
            ).fetchall()

        messages: List[BaseMessage] = []
        for row in reversed(rows):
            try:
                # json.loads 负责将字符串转为json
                # messages_from_dict 负责将 json 转为 LangChain 接收的 BaseMessage 列表
                messages.extend(messages_from_dict([json.loads(row[0])]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning("跳过无法解析的历史消息: session=%s", self.session_id)
        return messages

    # ============================================================
    # 写入: 继承父类默认实现 (只 INSERT, 不删除, 不触发摘要)
    # ============================================================
    # save_context 用 BaseChatMemory 默认实现:
    #   self.chat_memory.add_messages([HumanMessage, AIMessage])
    # 每轮对话仅 1 次 INSERT, 零 DELETE, 零 LLM 调用

    # ============================================================
    # 异步摘要: 对话结束后触发, 不阻塞主链路
    # ============================================================
    def maybe_summarize(self):
        """Generate one incremental summary batch without blocking chat."""
        try:
            state = self._load_summary_state()
            window_start_id = self._get_window_start_id(2 * self.k)
            if window_start_id is None:
                return

            pending_count = self._count_unsummarized_messages(
                state["summary_until_message_id"], window_start_id
            )
            trigger = max(1, int(self.summary_trigger_messages))
            if pending_count < trigger:
                logger.debug(
                    "incremental summary below threshold session=%s pending=%s trigger=%s",
                    self.session_id,
                    pending_count,
                    trigger,
                )
                return

            batch_size = max(1, int(self.summary_batch_size))
            messages, batch_end_id, raw_count = self._load_message_batch(
                state["summary_until_message_id"],
                window_start_id,
                batch_size,
            )
            if batch_end_id is None:
                return

            # 生成新的摘要并尝试提交，若失败则说明有其他 worker 已经提交了更新
            new_summary = self.predict_new_summary(messages, state["summary"])
            committed = self._commit_summary(
                summary=new_summary,
                old_version=state["summary_version"],
                old_cursor=state["summary_until_message_id"],
                new_cursor=batch_end_id,
                source_message_count=raw_count,
            )
            if committed:
                logger.info(
                    "incremental summary committed session=%s cursor=%s messages=%s version=%s",
                    self.session_id,
                    batch_end_id,
                    raw_count,
                    state["summary_version"] + 1,
                )
            else:
                logger.info(
                    "discarded stale summary result session=%s cursor=%s",
                    self.session_id,
                    batch_end_id,
                )
        except Exception as e:
            logger.warning("summary generation failed (conversation unaffected): %s", e)

    async def amaybe_summarize(self):
        """在线程中执行同步摘要，避免阻塞 asyncio 事件循环。"""
        import asyncio
        await asyncio.to_thread(self.maybe_summarize)

    # ============================================================
    # 摘要持久化
    # ============================================================
    def _summary_user_condition(self, params: Dict[str, Any]) -> str:
        """Return the user-isolation SQL fragment and populate params."""
        if self.user_id is None:
            return "user_id IS NULL"
        params["user_id"] = self.user_id
        return "user_id = :user_id"

    def _load_summary_state(self) -> Dict[str, Any]:
        """Load durable incremental-summary state for this session."""
        params = {"session_id": self.session_id}
        user_condition = self._summary_user_condition(params)
        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT summary, summary_until_message_id, "
                    "summary_version, source_message_count "
                    "FROM agent_summary_store "
                    "WHERE session_id = :session_id "
                    f"AND {user_condition}"
                ),
                params,
            ).fetchone()

        if not row:
            return {
                "summary": "",
                "summary_until_message_id": None,
                "summary_version": 0,
                "source_message_count": 0,
            }
        return {
            "summary": row[0] or "",
            "summary_until_message_id": int(row[1]) if row[1] is not None else None,
            "summary_version": int(row[2] or 0),
            "source_message_count": int(row[3] or 0),
        }

    def _get_window_start_id(self, window_size: int) -> Optional[int]:
        """Return the oldest id kept in the recent raw-message window."""
        if window_size <= 0:
            return None
        params = {"session_id": self.session_id, "limit": window_size}
        user_condition = self._summary_user_condition(params)
        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id FROM message_store "
                    "WHERE session_id = :session_id "
                    f"AND {user_condition} "
                    "ORDER BY id DESC LIMIT :limit"
                ),
                params,
            ).fetchall()
        if len(rows) < window_size:
            return None
        return int(rows[-1][0])

    def _count_unsummarized_messages(
        self,
        summary_cursor: Optional[int],
        window_start_id: int,
    ) -> int:
        """Count messages after the cursor and before the active window."""
        params = {
            "session_id": self.session_id,
            "window_start_id": window_start_id,
            "summary_cursor": summary_cursor or 0,
        }
        user_condition = self._summary_user_condition(params)
        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT COUNT(*) FROM message_store "
                    "WHERE session_id = :session_id "
                    f"AND {user_condition} "
                    "AND id > :summary_cursor "
                    "AND id < :window_start_id"
                ),
                params,
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def _load_message_batch(
        self,
        summary_cursor: Optional[int],
        window_start_id: int,
        limit: int,
    ) -> tuple[List[BaseMessage], Optional[int], int]:
        """Read one ordered batch and return (messages, last_id, raw_count)."""
        params = {
            "session_id": self.session_id,
            "summary_cursor": summary_cursor or 0,
            "window_start_id": window_start_id,
            "limit": limit,
        }
        user_condition = self._summary_user_condition(params)
        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, message FROM message_store "
                    "WHERE session_id = :session_id "
                    f"AND {user_condition} "
                    "AND id > :summary_cursor "
                    "AND id < :window_start_id "
                    "ORDER BY id ASC LIMIT :limit"
                ),
                params,
            ).fetchall()

        messages: List[BaseMessage] = []
        for row in rows:
            try:
                messages.extend(messages_from_dict([json.loads(row[1])]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning(
                    "skipping unparseable summary message session=%s message_id=%s",
                    self.session_id,
                    row[0],
                )
        last_id = int(rows[-1][0]) if rows else None
        return messages, last_id, len(rows)

    def _commit_summary(
        self,
        summary: str,
        old_version: int,
        old_cursor: Optional[int],
        new_cursor: int,
        source_message_count: int,
    ) -> bool:
        """Commit only if no other worker advanced this summary version."""
        params = {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "summary": summary,
            "old_version": old_version,
            "old_cursor": old_cursor,
            "new_cursor": new_cursor,
            "source_message_count": source_message_count,
        }
        user_condition = self._summary_user_condition(params)
        with get_engine().begin() as conn:
            # 如果是第一次提交摘要, 直接 INSERT, 否则 UPDATE 并检查版本号
            if old_version <= 0:
                try:
                    result = conn.execute(
                        text(
                            "INSERT INTO agent_summary_store "
                            "(session_id, user_id, summary, summary_until_message_id, "
                            "summary_version, source_message_count) "
                            "VALUES (:session_id, :user_id, :summary, :new_cursor, 1, "
                            ":source_message_count)"
                        ),
                        params,
                    )
                    return result.rowcount == 1
                except Exception:
                    logger.info("concurrent first summary insert session=%s", self.session_id)
                    return False

            result = conn.execute(
                text(
                    "UPDATE agent_summary_store SET "
                    "summary = :summary, "
                    "summary_until_message_id = :new_cursor, "
                    "summary_version = summary_version + 1, "
                    "source_message_count = :source_message_count, "
                    "updated_at = NOW() "
                    "WHERE session_id = :session_id "
                    f"AND {user_condition} "
                    "AND summary_version = :old_version "
                    "AND summary_until_message_id <=> :old_cursor"
                ),
                params,
            )
            return result.rowcount == 1

    def _load_summary(self) -> str:
        """
        读取摘要。
        从 MySQL agent_summary_store 读取。
        """
        return self._load_summary_state()["summary"]

    def _save_summary(self, summary: str):
        """
        保存摘要。
        UPSERT 到 MySQL agent_summary_store（含 user_id）。
        """
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO agent_summary_store "
                    "(session_id, user_id, summary) "
                    "VALUES (:session_id, :user_id, :summary) "
                    "ON DUPLICATE KEY UPDATE summary = :summary"
                ),
                {
                    "session_id": self.session_id,
                    "user_id": self.user_id,
                    "summary": summary,
                },
            )

    def clean_old_messages(self, days: int = 30):
        """
        手动触发清理旧消息。日常由 MySQL Event 自动执行。

        参数:
            days: 删除超过 N 天的消息
        """
        days = max(0, int(days))
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM message_store "
                    f"WHERE session_id = :session_id "
                    f"AND created_at < DATE_SUB(NOW(), INTERVAL {days} DAY)"
                ),
                {"session_id": self.session_id},
            )
        logger.info(f"清理旧消息: session={self.session_id}, days={days}")


# ============================================================
# 工厂函数
# ============================================================
from langchain_core.prompts import PromptTemplate
CUSTOM_SUMMARY_PROMPT = PromptTemplate(
    input_variables=["summary", "new_lines"],
    template="""你是一个光伏发电分析助手的对话摘要器。

任务：将下方新对话内容融入已有摘要，生成更新后的摘要。

要求：
- 保留用户身份、关注的站点名、偏好等关键信息
- 保留已查询过的数据结论（如某站某日发电量、预测偏差）
- 丢弃寒暄和无关闲聊
- 摘要长度控制在 200 字以内
- 用中文输出

已有摘要：
{summary}

新对话内容：
{new_lines}

更新后的摘要："""
)

def build_memory(
    session_id: str = "test",
    llm: Optional[BaseLanguageModel] = None,
    user_id: Optional[int] = None,
) -> PersistentWindowSummaryMemory:
    """
    构建 Agent 记忆。

    参数:
        session_id: 会话标识
        llm: 用于摘要的 LLM。不传则内部创建
        user_id: 用户ID,用于消息所有者隔离。None 时消息不绑定用户
    """

    # 如果未传入 LLM, 内部创建 (用于摘要生成)
    if llm is None:
        from backend.Agent.llm import build_llm
        llm = build_llm()

    from langchain_community.chat_message_histories import SQLChatMessageHistory
    from backend.app.database import get_engine
    # 使用自定义 converter, 让中文以原文存储 (非 \uXXXX 转义), 同时携带 user_id
    chat_memory = SQLChatMessageHistory(
        session_id=session_id,
        connection=get_engine(),
        table_name="message_store",
        custom_message_converter=ChineseFriendlyConverter("message_store", user_id=user_id),
    )

    return PersistentWindowSummaryMemory(
        k=3,
        llm=llm,
        chat_memory=chat_memory,
        memory_key="chat_history",
        return_messages=True,
        session_id=session_id,
        user_id=user_id,
        prompt=CUSTOM_SUMMARY_PROMPT
    )
