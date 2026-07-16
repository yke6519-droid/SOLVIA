"""通过 FastAPI 会话桥接器完成 Agent 与用户之间的交互。"""
from typing import Annotated, Callable, Optional

from langchain_core.tools import tool


_input_handler: Optional[Callable[[str], str]] = None


def set_input_handler(handler: Callable[[str], str]) -> None:
    """注册由 FastAPI 会话管理器提供的用户输入处理器。"""
    global _input_handler
    _input_handler = handler


def request_user_input(question: str) -> str:
    """通过当前请求绑定的 AskUserBridge 等待用户回复。"""
    if _input_handler is None:
        raise RuntimeError("用户输入处理器尚未初始化，请通过 FastAPI 会话调用该工具")
    return _input_handler(question)


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
    answer = request_user_input(question)
    if answer.startswith(("用户回复:", "用户回复：")):
        return answer
    return f"用户回复: {answer}"
