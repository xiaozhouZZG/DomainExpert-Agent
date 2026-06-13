"""重排器（BGE Reranker）"""
from typing import List, Dict, Any
import logging
import numpy as np
import os

logger = logging.getLogger(__name__)


class BGEReranker:
    """BGE 重排器 (bge-reranker-base)"""

    def __init__(self, model_path: str = None):
        """
        初始化 BGE 重排器

        Args:
            model_path: 本地模型路径，默认从环境变量 RERANKER_MODEL_PATH 读取
        """
        if model_path is None:
            model_path = os.getenv("RERANKER_MODEL_PATH", "BAAI/bge-reranker-base")

        try:
            from sentence_transformers import CrossEncoder

            self.model = CrossEncoder(model_path)
            logger.info(f"✓ 加载重排模型（离线）: {model_path}")

        except Exception as e:
            logger.error(f"重排模型加载失败: {str(e)}")
            raise

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """重排候选结果，分数归一化到 0-1"""
        if not candidates:
            return []

        # 构造输入对
        pairs = [(query, c["content"]) for c in candidates]

        # 批量打分（返回 logits）
        logits = self.model.predict(pairs)

        # Sigmoid 归一化到 0-1
        scores = 1 / (1 + np.exp(-logits))

        # 更新分数
        for i, candidate in enumerate(candidates):
            candidate["score"] = float(scores[i])

        # 排序并返回 top-k
        candidates.sort(key=lambda x: x["score"], reverse=True)

        return candidates[:top_k]
