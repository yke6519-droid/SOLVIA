"""
agent.py - AgentExecutor 组装
=============================
构建 Agent 的核心入口,组装 LLM + Tools + Prompt + Memory。
"""

from Agent.llm import build_llm
from Agent.tools import get_all_tools
from Agent.prompt import build_prompt
from Agent.memory import build_memory
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
    prompt = build_prompt()

    # 如果外部未传入 memory,内部创建
    if memory is None:
        memory = build_memory(session_id=session_id, use_db=use_db, llm=llm, user_id=user_id)

    agent = create_tool_calling_agent(llm, tools, prompt)

    agent_executor = AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,
        max_iterations=10,
        verbose=True,
        handle_parsing_errors=True,
        return_intermediate_steps=True,  # 不返回中间步骤,减少返回数据量
    )

    return agent_executor
