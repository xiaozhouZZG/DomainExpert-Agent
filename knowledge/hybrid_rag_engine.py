"""升级版 RAG 检索引擎（混合检索 + RRF + FAISS + 缓存 + 降级）

五步架构:
① 元数据预过滤 - 先用结构化条件缩小范围
② 混合检索 + RRF - 向量(FAISS HNSW) + BM25 并行召回，RRF 融合
③ CrossEncoder 重排 - 精排 top-k
④ 阈值兜底 - 低于阈值转人工
⑤ 语义缓存 - 相似 query 直接返回缓存

全程降级兜底:
- FAISS 不可用 → SQLite 暴力检索
- reranker 失败 → 用融合分数
- 缓存失败 → 直接检索
"""
import logging
import asyncio
import time
from typing import List, Dict, Any, Optional
import numpy as np

from database.connection import get_db_connection

logger = logging.getLogger(__name__)

# 全局单例
_hybrid_engine_instance = None


def get_hybrid_engine() -> "HybridRAGEngine":
    """获取混合检索引擎单例"""
    global _hybrid_engine_instance
    if _hybrid_engine_instance is None:
        _hybrid_engine_instance = HybridRAGEngine()
    return _hybrid_engine_instance


def reset_hybrid_engine() -> None:
    """Reset the singleton so the next access rebuilds the index from DB."""
    global _hybrid_engine_instance
    _hybrid_engine_instance = None


def refresh_index_after_ingest() -> None:
    """文档入库后刷新索引（不重复加载模型，不重复写入 chunk）。

    语义：reset 单例 → 获取新实例 → 触发 build_index()。
    失败时抛异常，由调用方决定是否假成功。
    """
    reset_hybrid_engine()
    engine = get_hybrid_engine()
    engine.build_index()


class HybridRAGEngine:
    """混合检索 RAG 引擎"""

    def __init__(self):
        # 加载配置
        self._load_config()
        self.mode = "vector"

        # 组件（延迟初始化）
        self.embedder = None
        self.reranker = None
        self.vector_index = None
        self.bm25 = None
        self.semantic_cache = None
        self.embedding_cache = None

        # 初始化组件（仅向量模式）
        self._init_components()

        # 索引是否已构建
        self._index_built = False

        logger.info(f"✓ HybridRAGEngine 初始化完成（向量+BM25混合）")

    def _load_config(self):
        """加载配置"""
        try:
            from config import settings
            self.vector_backend = settings.vector_backend
            self.recall_top_n = settings.recall_top_n
            self.rerank_top_k = settings.rerank_top_k
            self.final_top_k = settings.final_top_k
            self.relevance_threshold = settings.relevance_threshold
            self.rrf_k = settings.rrf_k
            self.enable_cache = settings.enable_semantic_cache
            self.cache_ttl = settings.cache_ttl
            self.cache_sim_threshold = settings.cache_similarity_threshold
            self.faiss_m = settings.faiss_m
            self.faiss_ef_construction = settings.faiss_ef_construction
            self.faiss_ef_search = settings.faiss_ef_search
            self.bm25_k1 = settings.bm25_k1
            self.bm25_b = settings.bm25_b
        except Exception as e:
            logger.warning(f"加载配置失败，使用默认值: {e}")
            self.vector_backend = "auto"
            self.recall_top_n = 100
            self.rerank_top_k = 10
            self.final_top_k = 5
            self.relevance_threshold = 0.35
            self.rrf_k = 60
            self.enable_cache = True
            self.cache_ttl = 3600
            self.cache_sim_threshold = 0.95
            self.faiss_m = 32
            self.faiss_ef_construction = 200
            self.faiss_ef_search = 100
            self.bm25_k1 = 1.5
            self.bm25_b = 0.75

    def _detect_mode(self) -> str:
        """检测运行模式"""
        try:
            import sentence_transformers  # noqa
            return "vector"
        except ImportError:
            logger.warning("未检测到 sentence-transformers，降级到关键词检索")
            return "keyword"

    def _init_components(self):
        """初始化所有组件（仅向量模式，无keyword降级）"""
        try:
            from .embedder import BGEEmbedder
            from .reranker import BGEReranker
            from .vector_index import create_vector_index
            from .hybrid_retriever import BM25Retriever
            # semantic_cache 是可选加速件：import 延后到缓存初始化处并包降级，
            # 避免该可选模块缺失/加载失败时拖垮整个引擎（embedder 等硬依赖照常 raise）。

            # 向量化器（必须成功）
            try:
                self.embedder = BGEEmbedder()
            except Exception as e:
                error_msg = f"BGE向量模型加载失败: {e}。请确认已安装 sentence-transformers 并能访问 Hugging Face。"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            # 重排器（失败不阻塞）
            try:
                self.reranker = BGEReranker()
            except Exception as e:
                logger.warning(f"重排器加载失败，将使用融合分数: {e}")
                self.reranker = None

            # 向量索引（FAISS HNSW 或降级）
            self.vector_index = create_vector_index(
                backend=self.vector_backend,
                dimension=self.embedder.dimension,
                M=self.faiss_m,
                ef_construction=self.faiss_ef_construction,
                ef_search=self.faiss_ef_search
            )

            # BM25 检索器（与向量混合使用，非降级）
            self.bm25 = BM25Retriever(k1=self.bm25_k1, b=self.bm25_b)

            # 缓存（可选加速件：缺失或加载失败时降级为无缓存，绝不拖垮引擎，与 reranker 同款降级）
            if self.enable_cache:
                try:
                    from .semantic_cache import SemanticCache, EmbeddingCache
                    self.semantic_cache = SemanticCache(
                        ttl=self.cache_ttl,
                        similarity_threshold=self.cache_sim_threshold
                    )
                    self.embedding_cache = EmbeddingCache()
                except Exception as e:
                    logger.warning(f"语义缓存加载失败，降级为无缓存模式: {e}")
                    self.semantic_cache = None
                    self.embedding_cache = None

            logger.info("✓ 所有检索组件初始化完成（向量+BM25混合模式）")

        except Exception as e:
            logger.error(f"组件初始化失败: {e}", exc_info=True)
            raise

    def build_index(self):
        """从数据库构建索引（FAISS + BM25）

        从 chunks 表读取所有数据，构建向量索引和 BM25 索引
        """
        if self.mode != "vector":
            logger.info("关键词模式，无需构建向量索引")
            return

        conn = get_db_connection()
        cursor = conn.cursor()

        # 读取所有 chunks（P0-5/P0-6: 带出商品隔离字段 product_name）
        cursor.execute("""
            SELECT c.chunk_id, c.text, c.embedding, c.category, c.source,
                   c.business_line, c.product_name, d.title
            FROM chunks c
            JOIN documents d ON c.doc_id = d.doc_id
            WHERE c.embedding IS NOT NULL
        """)

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            logger.warning("无可索引数据")
            return

        # 构建向量索引
        vectors = []
        ids = []
        metadatas = []
        bm25_docs = []

        for chunk_id, text, embedding_blob, category, source, business_line, product_name, doc_title in rows:
            embedding = np.frombuffer(embedding_blob, dtype=np.float32)
            vectors.append(embedding)
            ids.append(chunk_id)

            metadata = {
                "content": text,
                "source": source or doc_title,
                "category": category,
                "business_line": business_line,
                "product_name": product_name,
                "doc_title": doc_title
            }
            metadatas.append(metadata)

            bm25_docs.append({
                "id": chunk_id,
                "content": text,
                "metadata": metadata
            })

        # 添加到 FAISS
        vectors_array = np.array(vectors, dtype=np.float32)
        self.vector_index.add(vectors_array, ids, metadatas)

        # 构建 BM25 索引
        self.bm25.build_index(bm25_docs)

        self._index_built = True
        logger.info(f"✓ 索引构建完成: {len(rows)} 个 chunks")

    def search(
        self,
        query: str,
        top_k: int = None,
        threshold: float = None,
        filter_dict: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """混合检索（同步接口，兼容现有调用）

        Args:
            query: 查询文本
            top_k: 返回结果数（默认用配置）
            threshold: 阈值（默认用配置）
            filter_dict: 元数据过滤条件

        Returns:
            检索结果列表
        """
        # 使用配置默认值
        top_k = top_k or self.final_top_k
        threshold = threshold if threshold is not None else self.relevance_threshold

        # 确保索引已构建
        if self.mode == "vector" and not self._index_built:
            try:
                self.build_index()
            except Exception as e:
                logger.error(f"索引构建失败: {e}")

        # 运行混合检索
        try:
            # 检测是否已有事件循环在运行（如 Playwright browser_worker 线程）
            try:
                existing_loop = asyncio.get_running_loop()
            except RuntimeError:
                existing_loop = None

            if existing_loop is not None:
                # 已有事件循环运行中（如 Playwright 线程），
                # 在独立线程中执行异步检索，避免事件循环冲突
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(self._run_search_in_new_loop, query, top_k, threshold, filter_dict)
                    results = future.result(timeout=30)
            else:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                results = loop.run_until_complete(
                    self._hybrid_search_async(query, top_k, threshold, filter_dict)
                )
                loop.close()
            # P0-5: 统一把商品隔离字段提到结果顶层，保证 retrieval_gateway
            # 的 business_line 过滤器读得到（向量/BM25/重排三条路径口径一致）。
            return [self._promote_meta_fields(r) for r in results]
        except Exception as e:
            logger.error(f"混合检索失败: {e}", exc_info=True)
            raise RuntimeError(f"RAG检索失败: {e}。请检查向量模型和索引状态。")

    @staticmethod
    def _promote_meta_fields(result: Dict[str, Any]) -> Dict[str, Any]:
        """把 metadata 里的商品隔离字段提到结果顶层（不覆盖已有顶层值）。

        全链路统一：写入时 metadata 带这些字段，检索时统一从 metadata 提到顶层，
        避免「一边写 metadata，一边读顶层」导致过滤器恒空。
        """
        metadata = result.get("metadata") or {}
        for field in ("business_line", "category", "product_name", "source"):
            if result.get(field) is None:
                result[field] = metadata.get(field)
        return result

    def _run_search_in_new_loop(self, query: str, top_k: int, threshold: float, filter_dict: dict):
        """在独立线程中创建新事件循环执行异步检索，避免与 Playwright 事件循环冲突"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self._hybrid_search_async(query, top_k, threshold, filter_dict)
            )
        finally:
            loop.close()

    async def _hybrid_search_async(
        self,
        query: str,
        top_k: int,
        threshold: float,
        filter_dict: Optional[Dict]
    ) -> List[Dict[str, Any]]:
        """异步混合检索（核心逻辑）"""
        from .hybrid_retriever import reciprocal_rank_fusion

        # ===== 步骤⑤a: 检查语义缓存 =====
        query_embedding = self._embed_query(query)

        if self.semantic_cache:
            cached = self.semantic_cache.get(query_embedding)
            if cached is not None:
                logger.info("✓ 命中语义缓存")
                return cached[:top_k]

        # ===== 步骤②: 混合检索（向量 + BM25 并行）=====
        vector_task = asyncio.create_task(
            self._vector_retrieve_async(query_embedding, self.recall_top_n, filter_dict)
        )
        bm25_task = asyncio.create_task(
            self._bm25_retrieve_async(query, self.recall_top_n, filter_dict)
        )

        vector_results, bm25_results = await asyncio.gather(
            vector_task, bm25_task, return_exceptions=True
        )

        # 处理异常
        if isinstance(vector_results, Exception):
            logger.error(f"向量检索失败: {vector_results}")
            vector_results = []
        if isinstance(bm25_results, Exception):
            logger.error(f"BM25 检索失败: {bm25_results}")
            bm25_results = []

        logger.info(f"召回: 向量={len(vector_results)}, BM25={len(bm25_results)}")

        # ===== RRF 融合 =====
        if vector_results and bm25_results:
            fused = reciprocal_rank_fusion(
                [vector_results, bm25_results],
                k=self.rrf_k,
                id_key="id"
            )
        elif vector_results:
            fused = vector_results
        elif bm25_results:
            fused = bm25_results
        else:
            logger.warning("两路检索都无结果")
            return []

        # 取粗筛 top_n 进入重排
        candidates = fused[:self.recall_top_n]

        # 转换格式（reranker 需要 content 字段）
        for c in candidates:
            if "content" not in c and "metadata" in c:
                c["content"] = c["metadata"].get("content", "")

        # ===== 步骤③: CrossEncoder 重排 =====
        degraded_flag = False
        degraded_reason = None
        if self.reranker and candidates:
            try:
                reranked = self.reranker.rerank(query, candidates, top_k=self.rerank_top_k)
            except Exception as e:
                logger.error(f"重排失败，降级到余弦分: {e}")
                # P0-1: 回退到 RRF 前保存的向量余弦相似度，保持 0~1 量纲
                for c in candidates:
                    c["score"] = c.get("vector_score", 0.0)
                candidates.sort(key=lambda x: x["score"], reverse=True)
                reranked = candidates[:self.rerank_top_k]
                degraded_flag = True
                degraded_reason = "reranker_unavailable"
        else:
            # 没有 reranker：直接用余弦分
            for c in candidates:
                c["score"] = c.get("vector_score", 0.0)
            candidates.sort(key=lambda x: x["score"], reverse=True)
            reranked = candidates[:self.rerank_top_k]
            if self.reranker is None:
                degraded_flag = True
                degraded_reason = "reranker_not_loaded"

        # ===== 步骤④: 阈值兜底 =====
        if reranked:
            top_score = reranked[0].get("score", 0.0)
            if top_score < threshold:
                logger.info(f"重排最高分 {top_score:.3f} < 阈值 {threshold}，判定未命中")
                # 返回空，由上层走转人工
                final_results = []
            else:
                final_results = reranked[:top_k]
        else:
            final_results = []

        # P0-1: 降级信号写到每个结果上，透传到 retrieval_gateway
        if degraded_flag and final_results:
            for r in final_results:
                r["_degraded"] = True
                r["_degraded_reason"] = degraded_reason

        # ===== 步骤⑤b: 写入缓存 =====
        if self.semantic_cache and final_results:
            self.semantic_cache.put(query_embedding, final_results)

        return final_results

    def _embed_query(self, query: str) -> np.ndarray:
        """查询向量化（带缓存）"""
        # 检查嵌入缓存
        if self.embedding_cache:
            cached = self.embedding_cache.get(query)
            if cached is not None:
                return cached

        # BGE 检索指令前缀
        query_with_instruction = f"为这个句子生成表示以用于检索相关文章：{query}"
        embedding = self.embedder.embed(query_with_instruction)

        # 写入缓存
        if self.embedding_cache:
            self.embedding_cache.put(query, embedding)

        return embedding

    async def _vector_retrieve_async(
        self,
        query_embedding: np.ndarray,
        top_n: int,
        filter_dict: Optional[Dict]
    ) -> List[Dict[str, Any]]:
        """异步向量检索"""
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: self.vector_index.search(query_embedding, top_n, filter_dict)
        )

        # 统一格式
        formatted = []
        for r in results:
            metadata = r.get("metadata", {})
            formatted.append({
                "id": r["id"],
                "score": r["score"],
                "content": metadata.get("content", ""),
                "source": metadata.get("source", "未知"),
                "metadata": metadata
            })
        return formatted

    async def _bm25_retrieve_async(
        self,
        query: str,
        top_n: int,
        filter_dict: Optional[Dict]
    ) -> List[Dict[str, Any]]:
        """异步 BM25 检索"""
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: self.bm25.search(query, top_n, filter_dict)
        )
        return results

    def _vector_search_fallback(
        self,
        query: str,
        top_k: int,
        filter_dict: Optional[Dict]
    ) -> List[Dict[str, Any]]:
        """纯向量检索降级（混合检索失败时）"""
        try:
            query_embedding = self._embed_query(query)
            results = self.vector_index.search(query_embedding, top_k, filter_dict)

            formatted = []
            for r in results:
                metadata = r.get("metadata", {})
                formatted.append({
                    "content": metadata.get("content", ""),
                    "score": r["score"],
                    "source": metadata.get("source", "未知")
                })
            return formatted
        except Exception as e:
            logger.error(f"向量检索降级也失败: {e}")
            return []

    def _keyword_search(
        self,
        query: str,
        top_k: int,
        filter_dict: Optional[Dict]
    ) -> List[Dict[str, Any]]:
        """关键词检索（无 sentence-transformers 时降级）"""
        from .retriever import KeywordRetriever
        retriever = KeywordRetriever()
        return retriever.retrieve(query, top_k=top_k)

    def get_stats(self) -> Dict[str, Any]:
        """获取引擎统计信息"""
        stats = {
            "mode": self.mode,
            "index_built": self._index_built,
            "config": {
                "vector_backend": self.vector_backend,
                "recall_top_n": self.recall_top_n,
                "rerank_top_k": self.rerank_top_k,
                "relevance_threshold": self.relevance_threshold,
                "rrf_k": self.rrf_k,
            }
        }

        if self.vector_index:
            stats["vector_index"] = self.vector_index.get_stats()
        if self.semantic_cache:
            stats["semantic_cache"] = self.semantic_cache.get_stats()
        if self.embedding_cache:
            stats["embedding_cache"] = self.embedding_cache.get_stats()

        return stats
