import os
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""

    # RAG 模式（固定为 vector）
    rag_mode: str = Field(default="vector", alias="RAG_MODE")

    # LLM 配置
    llm_protocol: str = Field(default="openai", alias="LLM_PROTOCOL")
    llm_api_key: Optional[str] = Field(default=None, alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="gpt-4", alias="LLM_MODEL")

    # 数据库
    db_path: str = Field(default="platform.db", alias="DB_PATH")

    # 日志
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # API 服务
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8802, alias="API_PORT")

    # 模型配置（本地路径）
    embedding_model_path: str = Field(default="BAAI/bge-small-zh-v1.5", alias="EMBEDDING_MODEL_PATH")
    reranker_model_path: str = Field(default="BAAI/bge-reranker-base", alias="RERANKER_MODEL_PATH")

    # 知识库路径
    knowledge_base_path: str = Field(default="data/knowledge", alias="KNOWLEDGE_BASE_PATH")

    # 管理员密码
    admin_password: str = Field(default="admin123", alias="ADMIN_PASSWORD")

    # ========== RAG 检索配置（新增）==========
    # 向量索引后端: auto | faiss_hnsw | sqlite_bruteforce
    vector_backend: str = Field(default="auto", alias="VECTOR_BACKEND")

    # 混合检索: 粗筛召回数（向量+BM25各自召回这么多，再融合）
    recall_top_n: int = Field(default=100, alias="RECALL_TOP_N")

    # 重排: 精排后取 top-k
    rerank_top_k: int = Field(default=10, alias="RERANK_TOP_K")

    # 最终返回给 LLM 的结果数
    final_top_k: int = Field(default=5, alias="FINAL_TOP_K")

    # 相关度阈值（重排最高分低于此值 → 转人工）
    relevance_threshold: float = Field(default=0.35, alias="RELEVANCE_THRESHOLD")

    # RRF 融合参数
    rrf_k: int = Field(default=60, alias="RRF_K")

    # 语义缓存
    enable_semantic_cache: bool = Field(default=True, alias="ENABLE_SEMANTIC_CACHE")
    cache_ttl: int = Field(default=3600, alias="CACHE_TTL")  # 秒
    cache_similarity_threshold: float = Field(default=0.95, alias="CACHE_SIMILARITY_THRESHOLD")

    # FAISS HNSW 参数
    faiss_m: int = Field(default=32, alias="FAISS_M")  # 每节点最大邻居数
    faiss_ef_construction: int = Field(default=200, alias="FAISS_EF_CONSTRUCTION")
    faiss_ef_search: int = Field(default=100, alias="FAISS_EF_SEARCH")

    # BM25 参数
    bm25_k1: float = Field(default=1.5, alias="BM25_K1")
    bm25_b: float = Field(default=0.75, alias="BM25_B")

    # 是否启用量化（大规模数据省内存）
    enable_quantization: bool = Field(default=False, alias="ENABLE_QUANTIZATION")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# 全局配置实例
settings = Settings()
