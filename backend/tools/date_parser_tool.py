"""
date_parser_tool.py - 全局自然语言日期解析工具
=============================================
将用户输入的自然语言日期(如"今天""后天""7月3日""7.3""7-3")统一解析为 "YYYY-MM-DD" 格式。

对外暴露两个名字:
  - parse_date: LangChain @tool,供 LLM 调用
  - parse_flexible_date: 纯函数,供其他模块内部调用(如 pv_predictor)

使用方式:
  - LLM: 拿到用户自然语言日期后,先调 parse_date @tool 转成标准格式,再传给目标工具
  - 模块内部: from backend.tools.date_parser_tool import parse_flexible_date
"""
import re
from datetime import datetime, timedelta
from typing import Annotated
from langchain_core.tools import tool, ToolException


def _format_calendar_date(year: int, month: int, day: int, original: str) -> str:
    """校验年月日是否合法，并统一格式化为 YYYY-MM-DD。"""
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ToolException(f"无法解析日期: '{original}'，年月日不是有效的日历日期") from exc


def _parse_chinese_day_count(value: str) -> int | None:
    """解析相对日期中的天数，支持数字和常见中文数字。"""
    if value.isdigit():
        return int(value)

    chinese_numbers = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    return chinese_numbers.get(value)


def parse_flexible_date(date_str: str) -> str:
    """把多种日期格式统一解析成 "YYYY-MM-DD"。

    参数:
        date_str: 日期字符串,支持:
          - 相对日期: 今天、昨天、前天、大前天、明天、后天、大后天
          - 相对天数: 2天前、两天后、3 days ago、in 2 days
          - 标准格式: 2026-07-10
          - 中文格式: 7月10日、7月10号、2026年7月10日
          - 简写格式: 7-10、7/10、7.10、07-10、07.10
          - 带年份格式: 2026/07/10、2026.07.10
          - 空值: 默认返回今天

    返回:
        str: "YYYY-MM-DD" 格式日期

    异常:
        如果格式无法识别,抛出 ToolException 提示支持的格式。
    """
    if date_str is None:
        date_str = ""

    date_str = date_str.strip()

    now = datetime.now()

    # 空值和常见相对日期直接转换为偏移量，避免把“后天”等交给 LLM 猜测。
    relative_offsets = {
        "": 0,
        "今天": 0,
        "today": 0,
        "昨天": -1,
        "yesterday": -1,
        "前天": -2,
        "大前天": -3,
        "明天": 1,
        "tomorrow": 1,
        "后天": 2,
        "大后天": 3,
        "day before yesterday": -2,
        "day after tomorrow": 2,
    }
    if date_str.lower() in relative_offsets:
        return (now + timedelta(days=relative_offsets[date_str.lower()])).strftime("%Y-%m-%d")

    # 支持“2天前”“两天后”，以及常见英文表达“3 days ago”“in 2 days”。
    chinese_relative = re.fullmatch(r"([0-9一二两三四五六七八九十]+)\s*天\s*(前|后)", date_str)
    if chinese_relative:
        count = _parse_chinese_day_count(chinese_relative.group(1))
        if count is not None:
            offset = -count if chinese_relative.group(2) == "前" else count
            return (now + timedelta(days=offset)).strftime("%Y-%m-%d")

    english_relative = re.fullmatch(r"(?:in\s+)?(\d+)\s+days?(?:\s+ago)?", date_str.lower())
    if english_relative:
        count = int(english_relative.group(1))
        offset = -count if date_str.lower().endswith("ago") else count
        return (now + timedelta(days=offset)).strftime("%Y-%m-%d")

    # "YYYY-MM-DD" 标准格式
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass

    # "YYYY/MM/DD"、"YYYY.MM.DD" 带年份的分隔格式。
    for separator, date_format in (("/", "%Y/%m/%d"), (".", "%Y.%m.%d")):
        if separator in date_str:
            try:
                return datetime.strptime(date_str, date_format).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # "M月D日" / "M月D号" / "M月D" 中文格式(不带年份,默认补当前年)
    m = re.match(r"^(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?$", date_str)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        return _format_calendar_date(now.year, month, day, date_str)

    # "YYYY年M月D日" 带年份的中文格式
    m = re.match(r"^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?$", date_str)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _format_calendar_date(year, month, day, date_str)

    # "M-D" / "M/D" / "M.D" 月-日简写(不带年份,默认补当前年)。
    # 例如 2.3、2.3日、1.5日分别表示 2月3日、2月3日、1月5日。
    m = re.match(r"^(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[日号]?$", date_str)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        return _format_calendar_date(now.year, month, day, date_str)

    # 都不匹配,抛异常让 LLM 知道格式不对
    raise ToolException(
        f"无法解析日期: '{date_str}'。支持格式: '今天'、'昨天'、'前天'、'后天'、'大后天'、'2天后'、'YYYY-MM-DD'、'M月D日'、'M-D'、'M/D'、'M.D'、'YYYY年M月D日'"
    )


@tool
def parse_date(
    date_str: Annotated[str, "自然语言日期,支持: '今天'/'昨天'/'前天'/'后天'/'大后天'/'2天后'/'YYYY-MM-DD'/'M月D日'/'M-D'/'M/D'/'M.D'/'YYYY年M月D日'。传空字符串默认返回今天"],
) -> str:
    """将自然语言日期解析为标准 YYYY-MM-DD 格式。

    支持:
      - 相对日期: 今天、昨天、前天、大前天、明天、后天、大后天
      - 相对天数: 2天前、两天后、3 days ago、in 2 days
      - 标准格式: 2026-07-10
      - 中文格式: 7月10日、7月10号、2026年7月10日
      - 简写格式: 7-10、7/10、7.10、07-10、07.10
      - 带年份格式: 2026/07/10、2026.07.10
      - 空值: 默认返回今天

    返回:
        标准日期字符串,如 "2026-07-10"

    异常:
        如果格式无法识别,抛出 ToolException 提示支持的格式。
    """
    return parse_flexible_date(date_str)
