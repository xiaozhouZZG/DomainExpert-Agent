"""RAG 全链路引擎（纯向量检索）"""
import logging
from typing import List, Dict, Any, Optional
import os
import json
import numpy as np

from database.connection import get_db_connection

logger = logging.getLogger(__name__)

# 全局单例
_rag_engine_instance = None


def get_rag_engine() -> "RAGEngine":
    """获取 RAG 引擎单例"""
    global _rag_engine_instance
    if _rag_engine_instance is None:
        _rag_engine_instance = RAGEngine()
    return _rag_engine_instance


class RAGEngine:
    """RAG 全链路引擎（向量检索 + BM25 混合 + RRF 融合 + CrossEncoder 重排）"""

    def __init__(self):
        self._init_vector_components()
        logger.info("RAG Engine 初始化完成（向量检索模式）")

    def _init_vector_components(self):
        """初始化向量组件"""
        from .embedder import BGEEmbedder
        from .reranker import BGEReranker
        from .retriever import VectorRetriever

        # 设置 HF 镜像
        os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')

        try:
            self.embedder = BGEEmbedder()
            self.reranker = BGEReranker()
            self.retriever = VectorRetriever(embedder=self.embedder)
            logger.info("✓ 向量组件初始化成功")
        except Exception as e:
            logger.error(f"向量组件加载失败: {str(e)}")
            logger.error("请确保已安装 sentence-transformers 并能访问 Hugging Face")
            raise RuntimeError(f"RAG 引擎初始化失败: {e}") from e

    def ingest_document(
        self,
        file_path: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """摄取文档(解析 + 分块 + 向量化 + 入库)"""
        from .document_loader import DocumentLoader
        from .chunker import chunk_text
        import uuid
        from datetime import datetime

        try:
            text = DocumentLoader.load_document(file_path)
        except Exception as e:
            logger.error(f"文档解析失败: {str(e)}")
            return {"status": "failed", "error": str(e)}

        chunks = chunk_text(text, chunk_size=512, chunk_overlap=50)
        embeddings = self.embedder.embed_batch([c["text"] for c in chunks])

        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            doc_id = str(uuid.uuid4())
            now = datetime.now().isoformat()

            cursor.execute("""
                INSERT INTO documents (doc_id, file_path, title, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
            """, (
                doc_id,
                file_path,
                metadata.get("title", os.path.basename(file_path)) if metadata else os.path.basename(file_path),
                now,
                json.dumps(metadata, ensure_ascii=False) if metadata else "{}"
            ))

            for i, chunk in enumerate(chunks):
                chunk_id = f"{doc_id}_chunk_{i}"
                embedding_blob = embeddings[i].astype(np.float32).tobytes() if embeddings[i] is not None else None

                cursor.execute("""
                    INSERT INTO chunks (chunk_id, doc_id, chunk_index, text, embedding)
                    VALUES (?, ?, ?, ?, ?)
                """, (chunk_id, doc_id, i, chunk["text"], embedding_blob))

            conn.commit()

            logger.info(f"文档摄取成功: {file_path}, 生成 {len(chunks)} 个分块")

            return {
                "status": "success",
                "doc_id": doc_id,
                "chunks_count": len(chunks),
                "mode": "vector"
            }

        except Exception as e:
            logger.error(f"入库失败: {str(e)}")
            return {"status": "failed", "error": str(e)}
        finally:
            if conn:
                conn.close()

    def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.5
    ) -> List[Dict[str, Any]]:
        """检索知识库"""
        candidates = self.retriever.retrieve(query, top_k=top_k * 4)

        if not candidates:
            return []

        if self.reranker:
            reranked = self.reranker.rerank(query, candidates, top_k=top_k * 2)
        else:
            reranked = candidates[:top_k * 2]

        # 阈值过滤（如果 threshold > 0）
        if threshold > 0:
            filtered = [r for r in reranked if r["score"] >= threshold]
        else:
            filtered = reranked

        return filtered[:top_k] if filtered else []

    def get_stats(self) -> Dict[str, Any]:
        """获取知识库统计信息"""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM documents")
            doc_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM chunks")
            chunk_count = cursor.fetchone()[0]

            return {
                "mode": "vector",
                "documents": doc_count,
                "chunks": chunk_count,
                "embedding_model": "bge-small-zh-v1.5 (512维)",
                "reranker_model": "bge-reranker-base"
            }

        except Exception as e:
            return {"error": str(e)}
        finally:
            if conn:
                conn.close()
