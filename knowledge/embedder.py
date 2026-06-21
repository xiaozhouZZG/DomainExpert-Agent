"""向量化器（BGE）"""
import numpy as np
from typing import List
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _has_model_weights(path: Path) -> bool:
    return any(
        (path / filename).exists()
        for filename in ("model.safetensors", "pytorch_model.bin", "tf_model.h5", "flax_model.msgpack")
    )


def _default_embedding_model_path() -> str:
    env_value = os.getenv("EMBEDDING_MODEL_PATH")
    if env_value:
        return env_value
    local_saved = Path("models/bge-small-zh-v1.5")
    if local_saved.exists() and _has_model_weights(local_saved):
        return str(local_saved)
    local_snapshot_root = Path("models/models--BAAI--bge-small-zh-v1.5/snapshots")
    if local_snapshot_root.exists():
        snapshots = sorted((item for item in local_snapshot_root.iterdir() if item.is_dir()), key=lambda item: item.name)
        if snapshots:
            complete = [item for item in snapshots if _has_model_weights(item)]
            if complete:
                return str(complete[-1])
    return "BAAI/bge-small-zh-v1.5"


class BGEEmbedder:
    """BGE 向量化器 (bge-small-zh-v1.5, 512维)"""

    def __init__(self, model_path: str = None):
        """
        初始化 BGE 向量化器

        Args:
            model_path: 本地模型路径，默认从环境变量 EMBEDDING_MODEL_PATH 读取
        """
        if model_path is None:
            model_path = _default_embedding_model_path()

        try:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(model_path)
            self.dimension = 512  # bge-small-zh-v1.5 是 512 维
            logger.info(f"✓ 加载向量模型: {model_path}, 维度: {self.dimension}")

        except Exception as e:
            logger.error(f"向量模型加载失败: {str(e)}")
            raise

    def embed(self, text: str) -> np.ndarray:
        """单文本向量化"""
        return self.model.encode(text, normalize_embeddings=True)

    def embed_batch(self, texts: List[str]) -> List[np.ndarray]:
        """批量向量化"""
        embeddings = self.model.encode(texts, normalize_embeddings=True, batch_size=32)
        return [emb for emb in embeddings]
