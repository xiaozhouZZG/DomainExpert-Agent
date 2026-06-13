"""检索器（向量检索 + 关键词降级）"""
import numpy as np
from typing import List, Dict, Any
import logging
import re
from collections import Counter
import math

from database.connection import get_db_connection

logger = logging.getLogger(__name__)


class VectorRetriever:
    """向量检索器（余弦相似度）
    
    实现:
    - 当前为暴力全扫描 + Python 点积计算
    - 适用于 < 10万条文档
    
    生产优化:
    - 换 FAISS (Facebook 近似最近邻库)
    - 或 sqlite-vss (SQLite 向量扩展)
    - 或 pgvector (PostgreSQL 向量扩展)
    - 使用 ANN 算法 (HNSW/IVF) 加速检索
    """

    def __init__(self, embedder):
        self.embedder = embedder

    def retrieve(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """向量检索"""
        conn = None
        try:
            # 查询向量化（加 BGE 检索指令前缀）
            query_with_instruction = f"为这个句子生成表示以用于检索相关文章：{query}"
            query_embedding = self.embedder.embed(query_with_instruction)

            # 从数据库读取所有分块
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT c.text, c.embedding, d.title
                FROM chunks c
                JOIN documents d ON c.doc_id = d.doc_id
            """)

            rows = cursor.fetchall()

            if not rows:
                return []

            # 计算余弦相似度
            results = []
            for text, embedding_blob, doc_title in rows:
                if embedding_blob is None:
                    continue

                # 读取向量 (float32)
                chunk_embedding = np.frombuffer(embedding_blob, dtype=np.float32)

                # 余弦相似度（已归一化，直接点积）
                score = float(np.dot(query_embedding, chunk_embedding))

                # 确保分数在 0-1 范围
                score = max(0.0, min(1.0, score))

                results.append({
                    "content": text,
                    "score": score,
                    "source": doc_title
                })

            # 排序并返回 top-k
            results.sort(key=lambda x: x["score"], reverse=True)

            return results[:top_k]

        except Exception as e:
            logger.error(f"向量检索失败: {str(e)}")
            return []
        finally:
            if conn:
                conn.close()

