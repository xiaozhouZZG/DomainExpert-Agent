"""知识检索工具（RAG）"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import json
from typing import Optional

from .registry import register_tool


class SearchKnowledgeInput(BaseModel):
    """知识检索输入"""
    query: str = Field(..., description="检索问题")
    top_k: int = Field(5, description="返回结果数")
    threshold: float = Field(0.5, description="相似度阈值(0-1)")


@register_tool("search_knowledge")
@tool(args_schema=SearchKnowledgeInput)
def search_knowledge(
    query: str,
    top_k: int = 5,
    threshold: float = 0.5
) -> str:
    """
    检索知识库(RAG)

    参数:
        query: 检索问题
        top_k: 返回结果数(默认5)
        threshold: 相似度阈值 0-1(默认0.5)

    返回:
        JSON格式的检索结果(包含文本和引用来源)
    """
    try:
        from knowledge.hybrid_rag_engine import get_hybrid_engine

        rag = get_hybrid_engine()  # 使用混合检索单例
        results = rag.search(query, top_k=top_k, threshold=threshold)

        if not results:
            return json.dumps({"error": "未找到相关知识"}, ensure_ascii=False)

        # 格式化结果
        formatted = {
            "query": query,
            "results": [
                {
                    "content": r["content"],
                    "score": r["score"],
                    "source": r["source"],
                    "citation_id": i + 1
                }
                for i, r in enumerate(results)
            ]
        }

        return json.dumps(formatted, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": f"检索失败: {str(e)}"}, ensure_ascii=False)
