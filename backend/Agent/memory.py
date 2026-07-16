"""
memory.py - Agent 多轮对话记忆模块
=================================
架构: 窗口记忆 + 异步摘要 + MySQL 持久化

设计原则:
  1. 只追加不删除 — message_store 只 INSERT, 窗口在读取时截取
  2. 零阻塞 — 摘要异步生成, 不阻塞对话主链路
  3. 持久化 — 摘要和消息都落 MySQL, 重启可恢复
  4. 用户隔离 — 每条消息携带 user_id, 可按用户查询会话

测试环境: use_db=False, 纯内存
生产环境: use_db=True, MySQL 持久化
"""
import os
import json
import logging
from typing import Any, Dict, Optional, List

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain.memory.chat_memory import BaseChatMemory
from langchain.memory.summary import SummarizerMixin

logger = logging.getLogger(__name__)

# ============================================================
# 自定义 SQL 消息转换器（带 user_id）
# ============================================================
# 用 ensure_ascii=False 存储原始中文，同时携带 user_id 实现用户隔离。

from langchain_community.chat_message_histories.sql import DefaultMessageConverter
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, Text, String, DateTime, BigInteger, func


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
    summarize_threshold: int = 4  # 超出窗口多少条才触发摘要
    db_url: Optional[str] = None
    session_id: str = "default"
    user_id: Optional[int] = None  # 消息所有者ID,用于用户隔离
    moving_summary_buffer: str = ""  # 内存模式下的摘要

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
          1. 从 message_store 读取该 session 全部消息 (1 次 SELECT)
          2. 从 agent_summary_store 读取摘要 (1 次 SELECT)
          3. 截取最近 2*k 条作为窗口 (内存操作)
          4. 拼装: 摘要在前 + 窗口消息在后
        """
        # 1. 读取全部消息
        all_messages = self.chat_memory.messages

        # 2. 读取摘要
        summary = self._load_summary()

        # 3. 截取窗口
        window_size = 2 * self.k
        window = all_messages[-window_size:]

        # 4. 拼装
        result: List[BaseMessage] = []
        if summary:
            result.append(SystemMessage(content=f"之前对话摘要: {summary}"))
        result.extend(window)

        return {self.memory_key: result}

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
        """
        同步摘要: 对话结束后调用, 检查并生成摘要。
        供 FastAPI 对话任务和后台摘要任务复用。

        触发条件: 消息数 > 2*k + summarize_threshold
        失败不影响主流程。
        """
        try:
            all_messages = self.chat_memory.messages
            window_size = 2 * self.k
            threshold = window_size + self.summarize_threshold

            if len(all_messages) <= threshold:
                return  # 不需要摘要

            # 取出超出窗口的旧消息
            to_prune = all_messages[:len(all_messages) - window_size]

            # LLM 总结: 旧摘要 + 被裁剪的消息 -> 新摘要
            old_summary = self._load_summary()
            new_summary = self.predict_new_summary(to_prune, old_summary)

            # 保存摘要
            self._save_summary(new_summary)
            logger.info(
                f"摘要已更新: session={self.session_id}, "
                f"summarized {len(to_prune)} messages"
            )
        except Exception as e:
            logger.warning(f"摘要生成失败(不影响对话): {e}")

    async def amaybe_summarize(self):
        """在线程中执行同步摘要，避免阻塞 asyncio 事件循环。"""
        import asyncio
        await asyncio.to_thread(self.maybe_summarize)

    # ============================================================
    # 摘要持久化
    # ============================================================
    def _load_summary(self) -> str:
        """
        读取摘要。
        生产环境: 从 MySQL agent_summary_store 读取。
        测试环境: 从内存属性 moving_summary_buffer 读取。
        """
        if not self.db_url:
            return self.moving_summary_buffer

        import pymysql
        conn = pymysql.connect(**self._parse_mysql_url(self.db_url))
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT summary FROM agent_summary_store WHERE session_id=%s AND user_id=%s",
                    (self.session_id, self.user_id)
                )
                row = cur.fetchone()
                return row[0] if row else ""
        finally:
            conn.close()

    def _save_summary(self, summary: str):
        """
        保存摘要。
        生产环境: UPSERT 到 MySQL agent_summary_store（含 user_id）。
        测试环境: 写入内存属性 moving_summary_buffer。
        """
        if not self.db_url:
            self.moving_summary_buffer = summary
            return

        import pymysql
        conn = pymysql.connect(**self._parse_mysql_url(self.db_url))
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agent_summary_store (session_id, user_id, summary)
                       VALUES (%s, %s, %s)
                       ON DUPLICATE KEY UPDATE summary=%s""",
                    (self.session_id, self.user_id, summary, summary)
                )
            conn.commit()
        finally:
            conn.close()

    def _parse_mysql_url(self, url: str) -> dict:
        """解析 mysql+pymysql://user:pass@host:port/db 为 pymysql 参数"""
        from urllib.parse import urlparse
        p = urlparse(url)
        return dict(
            host=p.hostname,
            port=p.port or 3306,
            user=p.username,
            password=p.password,
            database=p.path.lstrip('/'),
            charset='utf8mb4',
        )

    def clean_old_messages(self, days: int = 30):
        """
        手动触发清理旧消息。日常由 MySQL Event 自动执行。

        参数:
            days: 删除超过 N 天的消息
        """
        if not self.db_url:
            return  # 内存模式无需清理

        import pymysql
        conn = pymysql.connect(**self._parse_mysql_url(self.db_url))
        try:
            with conn.cursor() as cur:

                cur.execute(
                    "DELETE FROM message_store "
                    "WHERE session_id=%s AND created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
                    (self.session_id, days)
                )
            conn.commit()
            logger.info(f"清理旧消息: session={self.session_id}, days={days}")
        finally:
            conn.close()


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
    use_db: bool = False,
    llm: Optional[BaseLanguageModel] = None,
    user_id: Optional[int] = None,
) -> PersistentWindowSummaryMemory:
    """
    构建 Agent 记忆。

    参数:
        session_id: 会话标识
        use_db: True=MySQL持久化, False=纯内存(测试)
        llm: 用于摘要的 LLM。不传则内部创建
        user_id: 用户ID,用于消息所有者隔离。None 时消息不绑定用户
    """

    # 如果未传入 LLM, 内部创建 (用于摘要生成)
    if llm is None:
        from backend.Agent.llm import build_llm
        llm = build_llm()

    if use_db:
        from langchain_community.chat_message_histories import SQLChatMessageHistory
        db_url = os.getenv("MYSQL_URL")
        # 使用自定义 converter, 让中文以原文存储 (非 \uXXXX 转义), 同时携带 user_id
        chat_memory = SQLChatMessageHistory(
            session_id=session_id,
            connection_string=db_url,
            table_name="message_store",
            custom_message_converter=ChineseFriendlyConverter("message_store", user_id=user_id),
        )
    else:
        from langchain_core.chat_history import InMemoryChatMessageHistory
        db_url = None
        chat_memory = InMemoryChatMessageHistory()

    return PersistentWindowSummaryMemory(
        k=3,
        llm=llm,
        chat_memory=chat_memory,
        memory_key="chat_history",
        return_messages=True,
        db_url=db_url,
        session_id=session_id,
        user_id=user_id,
        prompt=CUSTOM_SUMMARY_PROMPT
    )
