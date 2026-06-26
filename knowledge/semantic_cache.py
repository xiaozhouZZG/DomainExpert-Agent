"""语义缓存（内存 LRU + TTL）

设计:
1. 语义缓存: 对 query 嵌入，与缓存的 query 比相似度，高于阈值返回缓存
2. 嵌入缓存: 同一文本的 embedding 不重复计算
3. TTL: 缓存条目过期机制，避免返回过时答案

不依赖 Redis，纯内存实现（单机够用，多机需换 Redis）
"""
import time
import logging
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from collections import OrderedDict

logger = logging.getLogger(__name__)


class SemanticCache:
    """语义缓存

    对相似的 query 直接返回缓存的检索结果，避免重复检索
    """

    def __init__(
        self,
        max_size: int = 1000,
        ttl: int = 3600,
        similarity_threshold: float = 0.95
    ):
        """
        Args:
            max_size: 最大缓存条目数
            ttl: 过期时间（秒）
            similarity_threshold: 相似度阈值（高于此值视为同一查询）
        """
        self.max_size = max_size
        self.ttl = ttl
        self.similarity_threshold = similarity_threshold

        # 缓存结构: query_id -> {"embedding": ndarray, "results": [...], "timestamp": float}
        self.cache = OrderedDict()
        self.query_embeddings = []  # [(query_id, embedding), ...] 用于相似度匹配

        # 统计
        self.hits = 0
        self.misses = 0

    def get(self, query_embedding: np.ndarray) -> Optional[List[Dict[str, Any]]]:
        """查询缓存

        Args:
            query_embedding: 查询向量

        Returns:
            缓存的检索结果，未命中返回 None
        """
        now = time.time()

        # 清理过期缓存
        self._evict_expired(now)

        if not self.query_embeddings:
            self.misses += 1
            return None

        # 计算与所有缓存 query 的相似度
        best_score = 0.0
        best_query_id = None

        for query_id, cached_embedding in self.query_embeddings:
            # 余弦相似度（向量已归一化）
            score = float(np.dot(query_embedding, cached_embedding))
            if score > best_score:
                best_score = score
                best_query_id = query_id

        # 判断是否命中
        if best_score >= self.similarity_threshold and best_query_id in self.cache:
            entry = self.cache[best_query_id]

            # 检查 TTL
            if now - entry["timestamp"] <= self.ttl:
                # 命中！移到末尾（LRU）
                self.cache.move_to_end(best_query_id)
                self.hits += 1
                logger.info(f"✓ 语义缓存命中: similarity={best_score:.3f}")
                return entry["results"]

        self.misses += 1
        return None

    def put(self, query_embedding: np.ndarray, results: List[Dict[str, Any]]):
        """写入缓存

        Args:
            query_embedding: 查询向量
            results: 检索结果
        """
        now = time.time()

        # 生成唯一 ID
        query_id = f"q_{len(self.cache)}_{int(now * 1000) % 1000000}"

        # 写入缓存
        self.cache[query_id] = {
            "embedding": query_embedding,
            "results": results,
            "timestamp": now
        }
        self.query_embeddings.append((query_id, query_embedding))

        # LRU 淘汰
        while len(self.cache) > self.max_size:
            oldest_id, _ = self.cache.popitem(last=False)
            self.query_embeddings = [(qid, emb) for qid, emb in self.query_embeddings if qid != oldest_id]

        logger.debug(f"语义缓存写入: query_id={query_id}, 当前大小={len(self.cache)}")

    def _evict_expired(self, now: float):
        """清理过期缓存"""
        expired_ids = []
        for query_id, entry in self.cache.items():
            if now - entry["timestamp"] > self.ttl:
                expired_ids.append(query_id)

        for query_id in expired_ids:
            del self.cache[query_id]
            self.query_embeddings = [(qid, emb) for qid, emb in self.query_embeddings if qid != query_id]

        if expired_ids:
            logger.debug(f"清理 {len(expired_ids)} 个过期缓存")

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0.0

        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "ttl": self.ttl,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 3),
            "similarity_threshold": self.similarity_threshold
        }

    def clear(self):
        """清空缓存"""
        self.cache.clear()
        self.query_embeddings.clear()
        logger.info("语义缓存已清空")


class EmbeddingCache:
    """嵌入缓存（避免重复编码同一文本）"""

    def __init__(self, max_size: int = 5000):
        self.max_size = max_size
        self.cache = OrderedDict()  # text_hash -> embedding
        self.hits = 0
        self.misses = 0

    def get(self, text: str) -> Optional[np.ndarray]:
        """查询嵌入缓存"""
        text_hash = hash(text)

        if text_hash in self.cache:
            self.cache.move_to_end(text_hash)
            self.hits += 1
            return self.cache[text_hash]

        self.misses += 1
        return None

    def put(self, text: str, embedding: np.ndarray):
        """写入嵌入缓存"""
        text_hash = hash(text)
        self.cache[text_hash] = embedding

        # LRU 淘汰
        while len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0.0

        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 3)
        }

    def clear(self):
        """清空缓存"""
        self.cache.clear()
