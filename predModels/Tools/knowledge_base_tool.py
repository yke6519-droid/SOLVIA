"""
knowledge_base_tool.py - 知识库检索工具 (LangChain Tool)
============================================================
封装阿里云百炼知识库 Retrieve API，供 Agent 检索私有知识。

工具列表(@tool, 暴露给 LLM):
  1. search_knowledge_base  在百炼知识库中检索与用户问题相关的文档切片

设计说明:
  - 使用 alibabacloud_bailian20231229 SDK 调用 Retrieve API
  - AccessKey / Secret / WorkspaceId / IndexId 从 .env 读取
  - Client 懒加载(首次调用时创建),避免 Agent 启动时阻塞
  - SDK import 延迟到函数内部,未安装时不影响其他工具加载
  - 支持相似度阈值过滤(min_score),低于阈值的切片不返回,实现拒答
"""
import os
import json
from typing import Annotated

from langchain_core.tools import tool, ToolException
from dotenv import load_dotenv

load_dotenv()

# 百炼知识库配置(从 .env 读取)
_ACCESS_KEY_ID = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
_ACCESS_KEY_SECRET = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
_WORKSPACE_ID = os.getenv("WORKSPACE_ID", "")
_INDEX_ID = os.getenv("BAILIAN_INDEX_ID", "")
_ENDPOINT = os.getenv("BAILIAN_ENDPOINT", "bailian.cn-beijing.aliyuncs.com")

# 全局 Client(懒加载,类似 ModelManager 的思路)
_client = None


# ============================================================
# 内部辅助函数
# ============================================================

def _get_client():
    """
    懒加载百炼 SDK Client。

    首次调用时创建 Client 并缓存到全局变量 _client,
    后续调用直接返回缓存实例,避免重复创建连接。

    SDK 的 import 也放在这里(不在模块顶部),
    这样即使 SDK 未安装,模块加载也不会失败,其他工具照常工作。
    """
    global _client
    if _client is not None:
        return _client

    # 凭证校验
    if not _ACCESS_KEY_ID or not _ACCESS_KEY_SECRET:
        raise ToolException(
            "百炼 AccessKey 未配置,请在 .env 中设置 "
            "ALIBABA_CLOUD_ACCESS_KEY_ID 和 ALIBABA_CLOUD_ACCESS_KEY_SECRET"
        )
    if not _WORKSPACE_ID:
        raise ToolException("百炼 WORKSPACE_ID 未配置,请在 .env 中设置 WORKSPACE_ID")

    # 延迟 import SDK(未安装时报错而非模块加载失败)
    try:
        from alibabacloud_bailian20231229.client import Client as BailianClient
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError:
        raise ToolException(
            "百炼 SDK 未安装,请运行: pip install alibabacloud_bailian20231229"
        )

    config = open_api_models.Config(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
    )
    config.endpoint = _ENDPOINT
    _client = BailianClient(config)
    return _client


def _parse_metadata(metadata) -> dict:
    """
    安全解析 metadata(SDK 可能返回 dict 或 JSON 字符串)。

    百炼返回的 Metadata 字段格式不固定,有时是 dict,有时是 JSON 字符串。
    本函数统一转为 dict,解析失败返回空 dict。
    """
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            return json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


# ============================================================
# LangChain Tool (@tool, 暴露给 LLM)
# ============================================================

@tool
def search_knowledge_base(
    query: Annotated[str, "要在知识库中检索的问题或关键词"],
    top_k: Annotated[int, "返回的文档切片数量,默认5,范围1-20"] = 5,
    min_score: Annotated[float, "相似度阈值,低于此分数的切片不返回,默认0.01,范围0.01-1.0。建议保持低阈值让结果返回,由LLM自行判断相关性"] = 0.01,
) -> str:
    """在阿里云百炼知识库中检索与用户问题相关的文档内容。

    使用场景:用户询问光伏领域知识、设备规格、运维规范、政策法规等
    需要参考私有文档的问题时调用。返回与问题最相关的文档切片及其相似度分数。

    如果检索结果为空(相似度都低于阈值),说明本次查询未命中,
    应如实告知用户本次未检索到,但不要断言知识库中完全没有相关内容。

    返回:
        检索到的文档切片列表(含来源文件名、相似度分数、文本内容);
        若无匹配则返回未检索到内容的提示。
    """
    if not _INDEX_ID:
        raise ToolException("百炼 INDEX_ID 未配置,请在 .env 中设置 BAILIAN_INDEX_ID")

    # 延迟导入 SDK models(和 _get_client 中保持一致的懒加载策略)
    try:
        from alibabacloud_bailian20231229 import models as bailian_models
        from alibabacloud_tea_util import models as util_models
    except ImportError:
        raise ToolException(
            "百炼 SDK 未安装,请运行: pip install alibabacloud_bailian20231229"
        )

    # 参数范围校验
    top_k = max(1, min(top_k, 20))
    min_score = max(0.01, min(min_score, 1.0))

    try:
        client = _get_client()

        # 构建检索请求
        # - dense_similarity_top_k: 向量检索(语义)召回数
        # - sparse_similarity_top_k: 关键词检索召回数
        # - enable_reranking: 开启重排序,提升相关性(默认 true,覆盖知识库配置)
        # - rerank: 指定重排序模型(覆盖知识库创建时的配置)
        # - rerank_min_score: 相似度阈值,低于此分数的切片不返回(实现拒答的关键)
        request = bailian_models.RetrieveRequest(
            index_id=_INDEX_ID,
            query=query,
            dense_similarity_top_k=top_k,
            sparse_similarity_top_k=top_k,
            enable_reranking=True,
            rerank=[
                bailian_models.RetrieveRequestRerank(
                    model_name="gte-rerank-hybrid",
                )
            ],
            rerank_top_n=top_k,
            rerank_min_score=min_score,
        )

        headers = {}
        runtime = util_models.RuntimeOptions()
        response = client.retrieve_with_options(
            _WORKSPACE_ID, request, headers, runtime
        )

        # 错误处理:先检查 API 业务层返回的 code,
        # 区分"权限/配置错误"和"真正没搜到"两种情况。
        # 不检查会吞掉 403 等错误,误报为"未检索到相关内容"。
        # 注意:百炼 API 成功时 code="Success"(不是空字符串),
        # 只有错误时才返回 Index.xxx 等错误码。
        body = response.body
        if body and body.code and body.code != "Success":
            # API 返回了错误码(如 Index.NoWorkspacePermissions 表示无业务空间权限,
            # Index.InvalidParameter 表示参数错误等)
            raise ToolException(
                f"知识库 API 返回错误: {body.code} - {body.message}"
            )

        # 解析返回结果(走到这里说明 API 调用成功,只是可能没搜到内容)
        nodes = body.data.nodes if body.data else []
        if not nodes:
            return (
                f"本次检索(query='{query}')未返回达到阈值({min_score})的结果。"
                f"这仅代表当前查询未命中,不代表知识库中完全没有相关内容。"
                f"换一种问法或调整关键词可能检索到不同结果。"
            )

        # 格式化输出,供 LLM 参考回答
        results = []
        for i, node in enumerate(nodes, 1):
            score = f"{node.score:.2f}" if node.score is not None else "N/A"
            meta = _parse_metadata(node.metadata)
            doc_name = meta.get("doc_name", "未知来源")
            title = meta.get("title", "")

            header = f"--- 切片 {i} (相似度: {score}) ---"
            if title:
                header += f"\n标题: {title}"
            header += f"\n来源: {doc_name}"

            results.append(f"{header}\n内容: {node.text}\n")

        return "\n".join(results)

    except ToolException:
        raise
    except Exception as e:
        raise ToolException(f"知识库检索失败: {e}")
