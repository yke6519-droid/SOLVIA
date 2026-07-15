from langchain.memory import ConversationSummaryBufferMemory
from backend.Agent.llm import build_llm

def build_memory()->ConversationSummaryBufferMemory:
    memory = ConversationSummaryBufferMemory(
        llm = build_llm(),
        max_token_limit=1200,
        memory_key="chat_history",
        return_messages=True,  # 必须开启！返回 [HumanMessage, AIMessage] 列表
        summary_prompt="""总结光伏对话，保留模型路径、气象、发电量关键数据"""
    )
    return memory