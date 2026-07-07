"""
llm.py - LLM 构建器
==================
封装阿里云百炼 LLM 的初始化逻辑。
"""

import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("API_KEY")
base_url = os.getenv("BASE_URL")
model_name = os.getenv("MODEL_NAME")

def build_llm()-> ChatOpenAI:
    llmClient = ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        streaming=True,
        temperature=0.3,
    )
    return llmClient

