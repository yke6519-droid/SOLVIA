from langchain.memory import ConversationBufferMemory

def build_memory() -> ConversationBufferMemory:
    memory = ConversationBufferMemory(
        memory_key="chat_history",
        return_messages=True
    )
    return memory