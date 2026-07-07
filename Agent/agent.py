from Agent.llm import build_llm
from Agent.tools import get_all_tools
from Agent.prompt import build_prompt

from langchain.agents import create_tool_calling_agent, AgentExecutor

def build_agent() -> AgentExecutor:
    llm = build_llm()
    tools = get_all_tools()
    prompt = build_prompt()

    agent = create_tool_calling_agent(llm,tools,prompt)

    agent_executor = AgentExecutor(
        agent=agent,
        tools=tools,
        max_iterations=5,
        verbose=True,
        handle_parsing_errors=True,
        return_intermediate_steps=True
    )

    return agent_executor