FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量（强制离线模式）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖（全量安装，包括 LangGraph、向量检索等）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

# 创建模型目录
RUN mkdir -p /app/models

# 临时允许联网下载并保存 embedding 模型到镜像内固定路径
RUN unset HF_HUB_OFFLINE TRANSFORMERS_OFFLINE && \
    python -c "from sentence_transformers import SentenceTransformer; \
    model = SentenceTransformer('BAAI/bge-small-zh-v1.5'); \
    model.save('/app/models/bge-small-zh-v1.5'); \
    print('✓ Embedding model saved to /app/models/bge-small-zh-v1.5')"

# 临时允许联网下载并保存 reranker 模型到镜像内固定路径
RUN unset HF_HUB_OFFLINE TRANSFORMERS_OFFLINE && \
    python -c "from sentence_transformers.cross_encoder import CrossEncoder; \
    model = CrossEncoder('BAAI/bge-reranker-base'); \
    model.save('/app/models/bge-reranker-base'); \
    print('✓ Reranker model saved to /app/models/bge-reranker-base')"

# 复制项目文件
COPY . .

# 创建数据目录
RUN mkdir -p /app/data /app/data/knowledge /app/.cache

# 创建非 root 用户
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# 暴露端口
EXPOSE 8802

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8802/ || exit 1

# 启动命令
CMD ["python", "app.py"]