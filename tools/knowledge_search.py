"""知识检索工具（RAG）- 带置信度护栏"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import json
from typing import Optional

from .registry import register_tool


class SearchKnowledgeInput(BaseModel):
    """知识检索输入"""
    query: str = Field(..., description="检索问题")
    top_k: int = Field(5, description="返回结果数")
    business_line: Optional[str] = Field(None, description="业务线过滤")


@register_tool("search_knowledge")
@tool(args_schema=SearchKnowledgeInput)
def search_knowledge(
    query: str,
    top_k: int = 5,
    business_line: Optional[str] = None
) -> str:
    """
    检索知识库(RAG) - 带三段式置信度护栏

    参数:
        query: 检索问题
        top_k: 返回结果数(默认5)
        business_line: 业务线过滤(可选)

    返回:
        JSON格式的检索结果，包含：
        - status: 'high'(高置信度) | 'gray'(灰区) | 'not_found'(无答案)
        - confidence_score: 置信度分数
        - action: 推荐动作 ('answer' | 'handoff' | 'fallback')
        - results: 检索结果列表（高置信度时）
        - message: 提示信息（灰区/无答案时）

    注意：
    - status='high': 可以基于results作答（但硬事实仍需人审）
    - status='gray': 默认转人工，不要让模型硬答
    - status='not_found': 必须转人工或兜底话术
    """
    try:
        from knowledge.retrieval_gateway import search_with_confidence, format_retrieval_response

        # 使用统一检索出口
        retrieval_result = search_with_confidence(
            query=query,
            top_k=top_k,
            business_line=business_line
        )

        status = retrieval_result['status']
        confidence_score = retrieval_result['confidence_score']
        action = retrieval_result['action']

        # 格式化响应
        if status == 'high':
            # 高置信度：返回检索结果
            formatted = {
                "status": "high",
                "confidence_score": confidence_score,
                "action": "answer",
                "query": query,
                "results": [
                    {
                        "content": r["content"],
                        "score": r["score"],
                        "source": r.get("source", "unknown"),
                        "category": r.get("category", "unknown"),
                        "citation_id": i + 1
                    }
                    for i, r in enumerate(retrieval_result['results'])
                ],
                "note": "可以基于上述内容作答，但涉及价格、官方政策等硬事实仍需谨慎"
            }
        elif status == 'gray':
            # 灰区：不直接返回内容，建议转人工
            formatted = {
                "status": "gray",
                "confidence_score": confidence_score,
                "action": action,
                "query": query,
                "message": retrieval_result.get('message', '置信度不足，建议转人工处理'),
                "note": "⚠️ 灰区：宁可转人工，不要基于低置信度内容作答"
            }
        else:  # not_found
            # 无答案：明确告知
            formatted = {
                "status": "not_found",
                "confidence_score": confidence_score,
                "action": "handoff",
                "query": query,
                "message": retrieval_result.get('message', '知识库未找到相关信息'),
                "note": "必须转人工或使用兜底话术，不要编造答案"
            }

        return json.dumps(formatted, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": f"检索失败: {str(e)}",
            "action": "handoff"
        }, ensure_ascii=False)
