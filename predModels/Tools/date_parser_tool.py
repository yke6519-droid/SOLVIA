"""
date_parser_tool.py - 全局自然语言日期解析工具
=============================================
将用户输入的自然语言日期(如"今天""昨天""明天""7月3日""7-3")统一解析为 "YYYY-MM-DD" 格式。

对外暴露两个名字:
  - parse_date: LangChain @tool,供 LLM 调用
  - parse_flexible_date: 纯函数,供其他模块内部调用(如 pv_predictor)

使用方式:
  - LLM: 拿到用户自然语言日期后,先调 parse_date @tool 转成标准格式,再传给目标工具
  - 模块内部: from predModels.Tools.date_parser_tool import parse_flexible_date
"""
import re
from datetime import datetime, timedelta
from typing import Annotated
from langchain_core.tools import tool, ToolException


def parse_flexible_date(date_str: str) -> str:
    """把多种日期格式统一解析成 "YYYY-MM-DD"。

    参数:
        date_str: 日期字符串,支持:
          - 相对日期: 今天/today、昨天/yesterday、明天/tomorrow
          - 标准格式: 2026-07-10
          - 中文格式: 7月10日、7月10号、2026年7月10日
          - 简写格式: 7-10、07-10
          - 斜杠格式: 2026/07/10
          - 空值: 默认返回今天

    返回:
        str: "YYYY-MM-DD" 格式日期

    异常:
        如果格式无法识别,抛出 ToolException 提示支持的格式。
    """
    if date_str is None:
        date_str = ""

    date_str = date_str.strip()

    # 空值或"今天" → 当前日期
    if date_str == "" or date_str == "今天" or date_str == "today":
        return datetime.now().strftime("%Y-%m-%d")

    # "昨天" → 当前日期-1
    if date_str == "昨天" or date_str == "yesterday":
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # "明天" → 当前日期+1
    if date_str == "明天" or date_str == "tomorrow":
        return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    now = datetime.now()

    # "YYYY-MM-DD" 标准格式
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass

    # "M月D日" / "M月D号" / "M月D" 中文格式(不带年份,默认补当前年)
    m = re.match(r"^(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?$", date_str)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        return f"{now.year}-{month:02d}-{day:02d}"

    # "YYYY年M月D日" 带年份的中文格式
    m = re.match(r"^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?$", date_str)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{year}-{month:02d}-{day:02d}"

    # "M-D" / "MM-DD" 月-日简写(不带年份,默认补当前年)
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", date_str)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        return f"{now.year}-{month:02d}-{day:02d}"

    # "YYYY/MM/DD" 斜杠分隔
    try:
        return datetime.strptime(date_str.replace("/", "-"), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass

    # 都不匹配,抛异常让 LLM 知道格式不对
    raise ToolException(
        f"无法解析日期: '{date_str}'。支持格式: '今天'、'昨天'、'明天'、'YYYY-MM-DD'、'M月D日'、'M月D号'、'M-D'、'YYYY年M月D日'"
    )


@tool
def parse_date(
    date_str: Annotated[str, "自然语言日期,支持: '今天'/'昨天'/'明天'/'YYYY-MM-DD'/'M月D日'/'M月D号'/'M-D'/'YYYY年M月D日'。传空字符串默认返回今天"],
) -> str:
    """将自然语言日期解析为标准 YYYY-MM-DD 格式。

    支持:
      - 相对日期: 今天/today、昨天/yesterday、明天/tomorrow
      - 标准格式: 2026-07-10
      - 中文格式: 7月10日、7月10号、2026年7月10日
      - 简写格式: 7-10、07-10
      - 斜杠格式: 2026/07/10
      - 空值: 默认返回今天

    返回:
        标准日期字符串,如 "2026-07-10"

    异常:
        如果格式无法识别,抛出 ToolException 提示支持的格式。
    """
    return parse_flexible_date(date_str)
