"""
file_io_tool.py - 文件读写工具模块 (LangChain Tools)
=====================================================
提供文件写入和读取能力,供 LLM 在对话中直接调用。

工具列表(@tool,暴露给 LLM):
  1. write_file   将内容写入文件(用户要求创建/导出/保存时调用)
  2. read_file    读取指定文件内容
  3. verify_file  校验文件是否成功生成(write_file/export_table 后必须调用)

公共函数(供前端 API 直接调用):
  - write_file_to_bytes(content, filename)
      生成文件字节流,前端直接下载(不落盘)
  - read_file_from_bytes(file_bytes, filename)
      从前端上传的字节流读取内容(不需落盘)

设计说明:
  - 输出目录从 .env 的 FILE_DIR 读取,不硬编码
  - 支持 .txt 和 .md 两种格式
  - 用户未指定文件名时,根据内容自动生成标题(规则提取)
  - 预留 LLM 生成标题接口(use_llm 参数),后续可升级
  - 路径安全:防止 ../ 路径穿越
  - 前端对接:支持内存字节流,无需落盘即可读写
"""
import os
import re
import json
from datetime import datetime
from typing import Annotated, Optional

from langchain_core.tools import tool, ToolException
from dotenv import load_dotenv
from backend.app.services.attachment_service import resolve_bound_attachment_path
from backend.app.services.file_artifact_service import register_current_generated_file

load_dotenv()

# 输出目录从 .env 读取
FILE_DIR = os.getenv("FILE_DIR", "")

# 允许的文件后缀
ALLOWED_EXTENSIONS = (".txt", ".md")

# 文件名非法字符
ILLEGAL_CHARS = r'\/\\:*?"<>|'


# ============================================================
# 内部辅助函数
# ============================================================

def _ensure_extension(filename: str) -> str:
    """
    确保文件名有 .txt 或 .md 后缀。
    无后缀默认补 .txt。
    """
    if filename.endswith(".txt") or filename.endswith(".md"):
        return filename
    return filename + ".txt"


def _sanitize_filename(filename: str) -> str:
    """清理文件名中的非法字符,替换为下划线。"""
    for ch in ILLEGAL_CHARS:
        filename = filename.replace(ch, "_")
    return filename.strip()


def _generate_filename(
    content: str,
    use_llm: bool = False,
    llm=None,
) -> str:
    """
    根据内容生成文件名(不含后缀)。

    当前实现:规则提取——取前 30 字,截断到第一个标点或换行。
    预留接口:use_llm=True 时调 LLM 生成更智能的标题(后续实现)。

    参数:
        content: 文件内容
        use_llm: 是否用 LLM 生成标题(默认 False,当前未实现)
        llm: LLM 实例(use_llm=True 时需要)

    返回:
        文件名字符串(不含后缀)
    """
    if use_llm and llm is not None:
        # TODO: 后续实现 LLM 生成标题
        # prompt = "根据以下内容生成一个简短的文件标题(10字以内,不加标点):\n{content[:500]}"
        # return llm.invoke(prompt).content.strip()
        pass

    # 规则提取:取前 30 字,截断到第一个标点或换行
    preview = content.strip()[:30]
    for i, ch in enumerate(preview):
        if ch in "。，！？；,!?;\n":
            preview = preview[:i]
            break

    # 太短则补省略号
    if len(preview) < 3:
        preview = content.strip()[:20]

    # 清理非法字符
    preview = _sanitize_filename(preview)

    # 兜底:如果清理后为空,用时间戳
    if not preview:
        preview = f"导出文件_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    return preview


def _get_unique_filepath(filepath: str) -> str:
    """
    如果文件已存在,追加时间戳后缀避免覆盖。
    例:英杰发电分析.txt → 英杰发电分析_20260708_135000.txt
    """
    if not os.path.exists(filepath):
        return filepath

    base, ext = os.path.splitext(filepath)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{timestamp}{ext}"


def _list_files_in_dir() -> list:
    """列出输出目录下的所有文件名,供错误提示用。"""
    try:
        files = [f for f in os.listdir(FILE_DIR)
                 if os.path.isfile(os.path.join(FILE_DIR, f))]
        return sorted(files)
    except Exception:
        return []


# ============================================================
# LangChain Tools (@tool, 暴露给 LLM)
# ============================================================

@tool
def write_file(
    content: Annotated[str, "要写入文件的完整内容"],
    filename: Annotated[str, "文件名(可选)。不传则根据内容自动生成。支持 .txt 和 .md 格式"] = "",
) -> str:
    """将内容写入文件并返回结构化文件产物信息。

    支持场景:用户要求创建文件、导出文件、导出分析结果、保存报告等。
    如果未指定文件名,会根据内容自动生成。
    支持格式:.txt(纯文本)、.md(Markdown)。

    返回:
        写入成功后的 file_id、文件名和文件大小；不返回服务器真实路径或下载链接。
    """
    if not FILE_DIR:
        raise ToolException("FILE_DIR 未配置,请在 .env 中设置 FILE_DIR")

    # 确保目录存在
    os.makedirs(FILE_DIR, exist_ok=True)

    # 处理文件名
    if filename.strip():
        filename = _sanitize_filename(filename.strip())
        filename = _ensure_extension(filename)
    else:
        # 未提供文件名,根据内容自动生成
        filename = _generate_filename(content) + ".txt"

    # 拼接完整路径 + 防重名
    filepath = os.path.join(FILE_DIR, filename)
    filepath = _get_unique_filepath(filepath)

    # 写入文件
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        raise ToolException(f"文件写入失败: {e}")

    # 工具内部仍按原有方式落盘，但对 Agent 只返回 file_id，不暴露服务器真实路径。
    artifact = register_current_generated_file(
        filepath,
        filename=filename,
        source_tool="write_file",
    )
    if artifact and artifact.get("file_id"):
        return json.dumps(
            {
                "status": "ready",
                "result_type": "file",
                "message": "文件已生成，可以下载",
                "file": artifact,
            },
            ensure_ascii=False,
        )
    if artifact is not None:
        return json.dumps(
            {
                "status": "generated_unregistered",
                "result_type": "file",
                "message": "文件已生成，但下载记录暂时不可用",
                "file": None,
            },
            ensure_ascii=False,
        )
    # 离线直接调用工具时没有 Agent 请求上下文，保留原有兼容行为。
    return f"✅ 已写入文件: {filepath}"


@tool
def read_file(
    filename: Annotated[str, "要读取的历史文件名"] = "",
    attachment_id: Annotated[str, "当前对话附件ID"] = "",
) -> str:
    """读取指定文件的内容。

    支持场景:用户要求查看之前导出的文件、重新读取历史报告，或读取当前对话附件。
    历史文件使用 filename，当前对话附件使用 attachment_id。
    只能读取 temp/file 目录或服务端已授权的附件。支持 .txt 和 .md 格式。

    返回:
        文件的完整内容
    """
    if attachment_id:
        try:
            attachment, resolved_path = resolve_bound_attachment_path(attachment_id)
        except Exception as exc:
            raise ToolException(str(exc)) from exc
        filename = attachment["filename"]
        full_path = str(resolved_path)
    else:
        if not FILE_DIR:
            raise ToolException("FILE_DIR 未配置,请在 .env 中设置 FILE_DIR")

        # 路径安全:防止 ../ 路径穿越
        full_path = os.path.normpath(os.path.join(FILE_DIR, filename))
        if not full_path.startswith(os.path.normpath(FILE_DIR)):
            raise ToolException("文件名包含非法路径")

    # 检查文件是否存在
    if not os.path.exists(full_path):
        existing = _list_files_in_dir()
        if existing:
            hint = f"当前目录下有这些文件: {', '.join(existing)}"
        else:
            hint = "当前目录为空"
        return f"⏳ 文件不存在: {filename}\n{hint}"

    # 表格文件不按文本打开。旧实现只支持 txt/md，直接 open Excel 会得到
    # 二进制解码错误；现在统一转给 table_io_tool，并由它遍历完整工作簿。
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".xlsx", ".xls", ".csv"):
        from backend.tools.table_io_tool import (
            _generate_workbook_summary,
            _load_tabular_sheets,
        )

        sheets = _load_tabular_sheets(full_path, filename)
        return _generate_workbook_summary(sheets, filename)

    # 读取文本文件
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        raise ToolException(f"文件读取失败: {e}")

    return content


# ============================================================
# 文件校验工具
# ============================================================

def _format_file_size(size_bytes: int) -> str:
    """把字节数格式化为人类可读的大小。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


@tool
def verify_file(
    filename: Annotated[str, "要校验的历史文件名"] = "",
    attachment_id: Annotated[str, "当前对话附件ID"] = "",
) -> str:
    """校验文件是否成功生成。

    在 write_file 或 export_table 生成文件后调用，也可以校验当前对话附件。
    支持所有格式: .txt、.md、.xlsx、.xls、.csv。

    返回:
        校验结果(文件存在则返回大小、行列数等;不存在则报错)
    """
    if attachment_id:
        try:
            attachment, resolved_path = resolve_bound_attachment_path(attachment_id)
        except Exception as exc:
            raise ToolException(str(exc)) from exc
        filename = attachment["filename"]
        full_path = str(resolved_path)
    else:
        if not FILE_DIR:
            raise ToolException("FILE_DIR 未配置,请在 .env 中设置 FILE_DIR")

        # 路径安全:防止 ../ 路径穿越
        full_path = os.path.normpath(os.path.join(FILE_DIR, filename))
        if not full_path.startswith(os.path.normpath(FILE_DIR)):
            raise ToolException("文件名包含非法路径")

    # 检查文件是否存在
    if not os.path.exists(full_path):
        existing = _list_files_in_dir()
        if existing:
            hint = f"当前目录下有这些文件: {', '.join(existing)}"
        else:
            hint = "当前目录为空"
        return f"❌ 文件校验失败: {filename} 不存在\n{hint}"

    # 基础信息:大小 + 创建时间
    stat = os.stat(full_path)
    size_str = _format_file_size(stat.st_size)
    ctime_str = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")

    # 根据后缀做内容校验
    ext = os.path.splitext(filename)[1].lower()

    if ext in (".txt", ".md"):
        # 文本类:行数 + 字符数 + 前 3 行预览
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            char_count = sum(len(line) for line in lines)
            preview_lines = lines[:3]
            preview = "".join(f"  {line.rstrip()}\n" for line in preview_lines)
            return (
                f"✅ 文件校验通过: {filename}\n"
                f"  大小: {size_str}\n"
                f"  创建时间: {ctime_str}\n"
                f"  内容: {len(lines)} 行, {char_count} 字符\n"
                f"  前 3 行预览:\n{preview}"
            )
        except Exception as e:
            return f"❌ 文件校验失败: 文件存在但读取异常 - {e}"

    elif ext in (".xlsx", ".xls", ".csv"):
        # 表格类:完整工作簿的 Sheet 数量、行列数和列名。
        try:
            from backend.tools.table_io_tool import _load_tabular_sheets

            sheets = _load_tabular_sheets(full_path, filename)
            total_rows = sum(len(frame) for _, frame in sheets)
            sheet_details = "; ".join(
                f"{sheet_name}: {len(frame)} 行 × {len(frame.columns)} 列"
                for sheet_name, frame in sheets
            )
            return (
                f"✅ 文件校验通过: {filename}\n"
                f"  大小: {size_str}\n"
                f"  创建时间: {ctime_str}\n"
                f"  Sheet 数量: {len(sheets)}\n"
                f"  总数据行数: {total_rows}\n"
                f"  Sheet 明细: {sheet_details}"
            )
        except Exception as e:
            return f"❌ 文件校验失败: 文件存在但解析异常 - {e}"

    else:
        # 未知格式:只返回基础信息
        return (
            f"✅ 文件校验通过: {filename}\n"
            f"  大小: {size_str}\n"
            f"  创建时间: {ctime_str}"
        )


# ============================================================
# 公共函数(供前端 API 直接调用)
# ============================================================

def write_file_to_bytes(
    content: str,
    filename: str = "",
) -> dict:
    """
    生成文本文件字节流，供前端直接下载(不落盘)。

    前端对接流程:
      后端调本函数 → 返回 {filename, bytes, format}
      FastAPI 用 StreamingResponse 返回 bytes → 浏览器触发下载

    参数:
      content  — 文件内容
      filename — 文件名(可选，不传则自动生成)

    返回:
      {
        "filename": "英杰发电分析.txt",
        "bytes": b"...",
        "format": "txt",
        "size": 1234
      }
    """
    # 处理文件名
    if filename.strip():
        filename = _sanitize_filename(filename.strip())
        filename = _ensure_extension(filename)
    else:
        filename = _generate_filename(content) + ".txt"

    file_bytes = content.encode("utf-8")
    ext = os.path.splitext(filename)[1].lstrip(".")

    return {
        "filename": filename,
        "bytes": file_bytes,
        "format": ext,
        "size": len(file_bytes),
    }


def read_file_from_bytes(
    file_bytes: bytes,
    filename: str,
) -> dict:
    """
    从前端上传的字节流读取文本文件内容(不需落盘)。

    前端对接流程:
      用户上传文件 → FastAPI 拿到 file_bytes
      → 调本函数 → 返回 {content, filename, rows, chars}

    参数:
      file_bytes — 文件字节流(前端上传)
      filename   — 文件名(用于判断格式)

    返回:
      {
        "filename": "英杰发电分析.txt",
        "content": "文件完整内容...",
        "rows": 42,
        "chars": 1234,
        "format": "txt"
      }
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ToolException(f"不支持的文件格式: {ext}。支持 {', '.join(ALLOWED_EXTENSIONS)}")

    try:
        content = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = file_bytes.decode("gbk", errors="replace")

    lines = content.splitlines()

    return {
        "filename": filename,
        "content": content,
        "rows": len(lines),
        "chars": len(content),
        "format": ext.lstrip("."),
    }
