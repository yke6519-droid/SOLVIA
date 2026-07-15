"""
agent.py - AgentExecutor 组装
=============================
构建 Agent 的核心入口,组装 LLM + Tools + Prompt + Memory。
"""

from backend.Agent.llm import build_llm
from backend.Agent.tools import get_all_tools
from backend.tools.weather_fetcher_tool import get_current_datetime
from backend.Agent.prompt import build_prompt
from backend.Agent.memory import build_memory
from langchain.agents import create_tool_calling_agent, AgentExecutor
from typing import Optional


def build_agent(
    session_id: str = "test",
    use_db: bool = False,
    memory=None,
    user_id: Optional[int] = None,
) -> AgentExecutor:
    """
    构建 AgentExecutor。

    参数:
        session_id: 会话标识
        use_db: True=MySQL持久化记忆, False=纯内存(测试)
        memory: 外部传入的 memory 实例(优先于 session_id/use_db)
        user_id: 用户ID,用于消息所有者隔离

    返回:
        AgentExecutor 实例
    """
    llm = build_llm()
    tools = get_all_tools()
    # Agent 创建时固定注入一次服务端当前日期，避免模型自行猜测年份。
    current_datetime = get_current_datetime.invoke({})
    prompt = build_prompt(current_datetime=current_datetime)

    # 如果外部未传入 memory,内部创建
    if memory is None:
        memory = build_memory(session_id=session_id, use_db=use_db, llm=llm, user_id=user_id)

    agent = create_tool_calling_agent(llm, tools, prompt)

    agent_executor = AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,
        max_iterations=20,
        verbose=True,
        handle_parsing_errors=True,
        return_intermediate_steps=True,  # 不返回中间步骤,减少返回数据量
    )

    return agent_executor