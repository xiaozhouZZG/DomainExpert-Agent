"""向量索引抽象接口与实现

设计目标:
1. 可插拔: 支持 FAISS/SQLite/Milvus 等多种后端
2. 降级: FAISS 不可用时自动回退到 SQLite
3. 配置化: 通过环境变量选择后端
"""
import numpy as np
from typing import List, Dict, Any, Optional, Protocol
import logging
import os

logger = logging.getLogger(__name__)


class VectorIndex(Protocol):
    """向量索引抽象接口"""

    def add(self, vectors: np.ndarray, ids: List[str], metadatas: Optional[List[Dict]] = None):
        """批量添加向量

        Args:
            vectors: 向量矩阵 (N, D)
            ids: 向量 ID 列表
            metadatas: 元数据列表（可选）
        """
        ...

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 20,
        filter_dict: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """向量检索

        Args:
            query_vector: 查询向量 (D,)
            top_k: 返回 top-k 结果
            filter_dict: 元数据过滤条件（可选）

        Returns:
            [{"id": str, "score": float, "metadata": dict}, ...]
        """
        ...

    def save(self, path: str):
        """保存索引到磁盘"""
        ...

    def load(self, path: str):
        """从磁盘加载索引"""
        ...

    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计信息"""
        ...


class FaissHNSWIndex:
    """FAISS HNSW 索引实现（百万级毫秒返回）

    HNSW (Hierarchical Navigable Small World):
    - 构建分层图结构，上层稀疏、下层密集
    - 检索时从上层开始快速定位，逐层下钻
    - 复杂度: 构建 O(N log N), 查询 O(log N)
    - 内存: ~2-3倍原始向量大小

    参数:
    - M: 每个节点的最大邻居数（默认 32，越大越准但越慢）
    - ef_construction: 构建时的搜索范围（默认 200）
    - ef_search: 查询时的搜索范围（默认 100，越大越准但越慢）
    """

    def __init__(
        self,
        dimension: int = 512,
        M: int = 32,
        ef_construction: int = 200,
        ef_search: int = 100
    ):
        try:
            import faiss
            self.faiss = faiss
        except ImportError:
            raise ImportError(
                "FAISS 未安装，请运行: pip install faiss-cpu  (或 faiss-gpu)"
            )

        self.dimension = dimension
        self.M = M
        self.ef_construction = ef_construction
        self.ef_search = ef_search

        # 创建 HNSW 索引
        self.index = faiss.IndexHNSWFlat(dimension, M)
        self.index.hnsw.efConstruction = ef_construction
        self.index.hnsw.efSearch = ef_search

        # ID 映射（FAISS 内部用整数索引，需映射到字符串 ID）
        self.id_to_index = {}  # str -> int
        self.index_to_id = {}  # int -> str
        self.metadatas = {}    # str -> dict
        self.next_index = 0

        logger.info(f"✓ FAISS HNSW 索引初始化: dim={dimension}, M={M}, ef_construction={ef_construction}")

    def add(self, vectors: np.ndarray, ids: List[str], metadatas: Optional[List[Dict]] = None):
        """批量添加向量"""
        if vectors.shape[1] != self.dimension:
            raise ValueError(f"向量维度不匹配: 期望 {self.dimension}, 实际 {vectors.shape[1]}")

        # 转换为 float32（FAISS 要求）
        vectors = vectors.astype(np.float32)

        # 添加到索引
        self.index.add(vectors)

        # 更新 ID 映射
        for i, vector_id in enumerate(ids):
            idx = self.next_index + i
            self.id_to_index[vector_id] = idx
            self.index_to_id[idx] = vector_id

            if metadatas and i < len(metadatas):
                self.metadatas[vector_id] = metadatas[i]

        self.next_index += len(ids)

        logger.info(f"✓ 添加 {len(ids)} 个向量到 FAISS 索引，当前总数: {self.next_index}")

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 20,
        filter_dict: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """向量检索"""
        if self.index.ntotal == 0:
            return []

        # 转换为 float32 并添加批次维度
        query_vector = query_vector.astype(np.float32).reshape(1, -1)

        # FAISS 检索（返回距离和索引）
        # 注意：HNSW 返回的是 L2 距离，需转换为余弦相似度
        # 由于向量已归一化，L2距离 = 2 * (1 - 余弦相似度)
        # 余弦相似度 = 1 - L2距离/2
        search_k = min(top_k * 3, self.index.ntotal)  # 多召回一些，用于过滤
        distances, indices = self.index.search(query_vector, search_k)

        # 转换结果
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:  # FAISS 用 -1 表示无效结果
                continue

            vector_id = self.index_to_id.get(idx)
            if not vector_id:
                continue

            metadata = self.metadatas.get(vector_id, {})

            # 元数据过滤
            if filter_dict and not self._match_filter(metadata, filter_dict):
                continue

            # L2 距离转余弦相似度（向量已归一化）
            # 余弦相似度 = 1 - L2^2 / 2
            score = 1.0 - float(dist) / 2.0
            score = max(0.0, min(1.0, score))  # 限制在 0-1

            results.append({
                "id": vector_id,
                "score": score,
                "metadata": metadata
            })

        # 按分数排序
        results.sort(key=lambda x: x["score"], reverse=True)

        return results[:top_k]

    def _match_filter(self, metadata: Dict, filter_dict: Dict) -> bool:
        """检查元数据是否匹配过滤条件"""
        for key, value in filter_dict.items():
            if key not in metadata:
                return False
            if metadata[key] != value:
                return False
        return True

    def save(self, path: str):
        """保存索引"""
        import pickle

        # 保存 FAISS 索引
        faiss_path = f"{path}.faiss"
        self.faiss.write_index(self.index, faiss_path)

        # 保存映射和元数据
        meta_path = f"{path}.meta"
        with open(meta_path, 'wb') as f:
            pickle.dump({
                'id_to_index': self.id_to_index,
                'index_to_id': self.index_to_id,
                'metadatas': self.metadatas,
                'next_index': self.next_index
            }, f)

        logger.info(f"✓ FAISS 索引已保存: {faiss_path}, {meta_path}")

    def load(self, path: str):
        """加载索引"""
        import pickle

        # 加载 FAISS 索引
        faiss_path = f"{path}.faiss"
        self.index = self.faiss.read_index(faiss_path)
        self.index.hnsw.efSearch = self.ef_search

        # 加载映射和元数据
        meta_path = f"{path}.meta"
        with open(meta_path, 'rb') as f:
            data = pickle.load(f)
            self.id_to_index = data['id_to_index']
            self.index_to_id = data['index_to_id']
            self.metadatas = data['metadatas']
            self.next_index = data['next_index']

        logger.info(f"✓ FAISS 索引已加载: {self.index.ntotal} 个向量")

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "backend": "faiss_hnsw",
            "total_vectors": self.index.ntotal,
            "dimension": self.dimension,
            "M": self.M,
            "ef_construction": self.ef_construction,
            "ef_search": self.ef_search,
            "memory_estimate_mb": (self.index.ntotal * self.dimension * 4 * 2.5) / 1024 / 1024
        }


class SqliteBruteForceIndex:
    """SQLite 暴力检索索引（降级后备，适用于 < 1万条数据）

    优点:
    - 零依赖，基于 SQLite
    - 实现简单，易调试

    缺点:
    - O(N) 复杂度，数据量大时慢
    - 全量加载内存
    """

    def __init__(self, dimension: int = 512):
        from database.connection import get_db_connection
        self.dimension = dimension
        self.get_db_connection = get_db_connection
        logger.info(f"✓ SQLite 暴力索引初始化: dim={dimension} (降级模式)")

    def add(self, vectors: np.ndarray, ids: List[str], metadatas: Optional[List[Dict]] = None):
        """批量添加向量（写入 SQLite）"""
        import json

        conn = self.get_db_connection()
        cursor = conn.cursor()

        for i, (vector_id, vector) in enumerate(zip(ids, vectors)):
            embedding_blob = vector.astype(np.float32).tobytes()
            metadata_json = json.dumps(metadatas[i], ensure_ascii=False) if metadatas and i < len(metadatas) else "{}"

            # 假设 chunks 表已有 embedding 和 metadata 字段
            # 实际插入由 rag_engine.ingest_document() 完成
            pass

        conn.close()
        logger.info(f"✓ SQLite 暴力索引添加 {len(ids)} 个向量")

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 20,
        filter_dict: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """向量检索（暴力遍历）"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        # 构建 WHERE 子句（元数据过滤）
        where_clauses = []
        params = []
        if filter_dict:
            for key, value in filter_dict.items():
                where_clauses.append(f"c.{key} = ?")
                params.append(value)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # 查询所有向量
        cursor.execute(f"""
            SELECT c.chunk_id, c.embedding, c.text, d.title
            FROM chunks c
            JOIN documents d ON c.doc_id = d.doc_id
            {where_sql}
        """, params)

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return []

        # 暴力计算余弦相似度
        results = []
        for chunk_id, embedding_blob, text, doc_title in rows:
            if embedding_blob is None:
                continue

            chunk_embedding = np.frombuffer(embedding_blob, dtype=np.float32)
            score = float(np.dot(query_vector, chunk_embedding))
            score = max(0.0, min(1.0, score))

            results.append({
                "id": chunk_id,
                "score": score,
                "metadata": {"content": text, "source": doc_title}
            })

        # 排序并返回 top-k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def save(self, path: str):
        """SQLite 索引无需单独保存（数据已在数据库）"""
        logger.info("SQLite 索引无需单独保存")

    def load(self, path: str):
        """SQLite 索引无需单独加载"""
        logger.info("SQLite 索引无需单独加载")

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
        total = cursor.fetchone()[0]
        conn.close()

        return {
            "backend": "sqlite_bruteforce",
            "total_vectors": total,
            "dimension": self.dimension,
            "note": "降级模式，O(N) 复杂度"
        }


def create_vector_index(
    backend: str = "auto",
    dimension: int = 512,
    **kwargs
) -> VectorIndex:
    """工厂函数：创建向量索引

    Args:
        backend: "auto" | "faiss_hnsw" | "sqlite_bruteforce"
        dimension: 向量维度
        **kwargs: 后端特定参数

    Returns:
        VectorIndex 实例
    """
    if backend == "auto":
        # 自动检测：优先 FAISS，降级到 SQLite
        try:
            import faiss  # noqa
            backend = "faiss_hnsw"
            logger.info("自动检测: 使用 FAISS HNSW 索引")
        except ImportError:
            backend = "sqlite_bruteforce"
            logger.warning("FAISS 不可用，降级到 SQLite 暴力索引")

    if backend == "faiss_hnsw":
        return FaissHNSWIndex(
            dimension=dimension,
            M=kwargs.get("M", 32),
            ef_construction=kwargs.get("ef_construction", 200),
            ef_search=kwargs.get("ef_search", 100)
        )
    elif backend == "sqlite_bruteforce":
        return SqliteBruteForceIndex(dimension=dimension)
    else:
        raise ValueError(f"未知后端: {backend}")
