import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
api_key = os.getenv("API_KEY")
base_url = os.getenv("BASE_URL")
model_name = os.getenv("MODEL_NAME")

"""
    初始化LLM
    项目其他位置可以通过这个方法创建新的LLM
"""


def createLLM():
    llmClient = OpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model_name
    )
    return llmClient
