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
    """搜索知识库"""
    rag = get_hybrid_engine()
    results = rag.search(query, top_k=top_k, threshold=0)
    return {"query": query, "results": results}
