"""
ask_user_tool.py - 用户交互工具
================================
让 LLM 在工具执行链路中间向用户提问并等待回复。

CLI 阶段: 默认用 input() 阻塞等待用户输入
FastAPI 阶段: 调用 set_input_handler() 替换为异步回调,工具代码不用改

使用场景:
  - 站点模糊匹配需要用户选择
  - 耗时操作前让用户确认
  - 导出格式/路径等需要用户指定
"""
from typing import Annotated, Callable
from langchain_core.tools import tool


# 可注入的输入处理器,默认为内置 input (CLI 阻塞输入)
_input_handler: Callable[[str], str] = input


def set_input_handler(handler: Callable[[str], str]) -> None:
    """替换输入处理器(供 FastAPI 等非 CLI 环境使用)。

    CLI 环境: 不需要调用,默认用 input() 即可
    FastAPI 环境: 替换为异步回调机制

    参数:
        handler: 接收提示语、返回用户输入字符串的函数
    """
    global _input_handler
    _input_handler = handler


@tool
def ask_user(
    question: Annotated[str, "要向用户提出的问题,需清晰描述需要用户确认或选择的内容"],
) -> str:
    """向用户提问并等待回复。

    适用场景:
      - 站点模糊匹配: 查到多个站点,需要用户选择具体哪个
      - 耗时操作前确认: 如加载模型预测前,询问用户是否继续
      - 导出格式确认: 用户要求导出但未指定格式,询问 xlsx 还是 csv
      - 信息补充: 缺少必要参数(如日期),需要用户提供

    不适用场景:
      - 能从对话历史中获取的信息(如之前提过的站点名)
      - 能通过工具查询的信息(如站点列表,应调 get_station_info)

    返回:
        用户的回复内容,带"用户回复:"前缀,便于 LLM 识别
    """
    print(f"\n🤔 {question}")
    answer = _input_handler(question)
    return f"用户回复: {answer}"
