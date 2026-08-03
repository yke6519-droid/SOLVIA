"""
llm.py - LLM 构建器
==================
封装阿里云百炼 LLM 的初始化逻辑。
"""

import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

def _build_chat_llm(
    *,
    model: str | None,
    streaming: bool,
    temperature: float,
    extra_body: dict | None = None,
) -> ChatOpenAI:
    """按统一连接配置创建一个 LLM 客户端。"""

    kwargs = {
        "api_key": os.getenv("API_KEY"),
        "base_url": os.getenv("BASE_URL"),
        "model": model,
        "streaming": streaming,
        "temperature": temperature,
    }
    if streaming:
        # 只有主 Agent LLM 需要把 token 用量带回现有流式链路。
        kwargs["stream_usage"] = True
    if extra_body:
        kwargs["extra_body"] = extra_body
    return ChatOpenAI(**kwargs)


def _configured_model(env_name: str) -> str | None:
    """允许某个角色使用独立模型；未配置时回退到主模型。"""

    return os.getenv(env_name) or os.getenv("MODEL_NAME")


def build_llm() -> ChatOpenAI:
    """创建主 Agent LLM：负责规划和调用工具，保持低温度。"""

    return _build_chat_llm(
        model=_configured_model("MODEL_NAME"),
        streaming=True,
        temperature=0.1,
    )


def build_intent_llm() -> ChatOpenAI:
    """创建意图识别 LLM：非流式、低温度，不强制调用函数。"""

    return _build_chat_llm(
        model=_configured_model("INTENT_MODEL_NAME"),
        streaming=False,
        temperature=0.0,
        # Qwen 思考模式不适合当前的强约束 JSON 意图输出，显式关闭。
        extra_body={"enable_thinking": False},
    )

