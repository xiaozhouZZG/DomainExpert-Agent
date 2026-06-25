"""知识库管理接口"""
from fastapi import APIRouter
from knowledge.hybrid_rag_engine import get_hybrid_engine

router = APIRouter()


@router.get("/api/kb/stats")
async def get_kb_stats():
    """获取知识库统计"""
    rag = get_hybrid_engine()
    return rag.get_stats()


@router.post("/api/kb/search")
async def search_kb(query: str, top_k: int = 5):
    """搜索知识库 — 走统一检索出口（三段式护栏）"""
    from knowledge.retrieval_gateway import search_with_confidence
    result = search_with_confidence(query=query, top_k=top_k)
    return {
        "query": query,
        "results": result.get("results", []),
        "confidence_score": result.get("confidence_score", 0.0),
        "decision": result.get("status", "not_found"),
        "action": result.get("action", "handoff"),
        "thresholds": result.get("threshold_config", {}),
        "message": result.get("message"),
    }
