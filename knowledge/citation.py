"""引用溯源模块"""
from typing import List, Dict, Any


def add_citations(results: List[Dict[str, Any]]) -> str:
    """为检索结果添加引用标记"""
    if not results:
        return ""

    context_parts = []
    for i, result in enumerate(results):
        citation_id = i + 1
        content = result.get("content", "")
        source = result.get("source", "未知来源")

        context_parts.append(f"[{citation_id}] {content}\n来源: {source}")

    return "\n\n".join(context_parts)


def format_citations(results: List[Dict[str, Any]]) -> List[str]:
    """格式化引用列表"""
    return [f"[{i+1}] {r.get('source', '未知来源')}" for i, r in enumerate(results)]
