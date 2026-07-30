"""R3 人机交互协议的纯函数和稳定标识生成。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def make_confirmation_key(
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """为一次确认生成稳定业务指纹。

    参数使用排序后的 JSON 序列化，因此字典字段顺序不同不会导致重复询问。
    原始参数不放入 key，避免把站点名称等业务内容直接暴露在交互标识中。
    """

    canonical_arguments = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical_arguments.encode("utf-8")).hexdigest()[:16]
    return f"{tool_name}:{digest}"


__all__ = ["make_confirmation_key"]
