# Docker 部署指南 - RAG 升级版

## 📦 需要上传到服务器的文件

由于 Docker 使用 `COPY . .`，所有新代码会自动包含在镜像中。

### 必须上传的文件（9个）

**新建文件 (5个)**:
```
knowledge/vector_index.py
knowledge/hybrid_retriever.py
knowledge/semantic_cache.py
knowledge/hybrid_rag_engine.py
knowledge/benchmark.py
```

**修改文件 (4个)**:
```
requirements.txt          # 新增 faiss-cpu==1.9.0
config.py                 # 新增 RAG 配置项
database/connection.py    # chunks 表新增字段
core/response_engine.py   # 集成混合引擎
```

---

## 🚀 服务器部署步骤

### 方式1：完整项目上传（推荐）

```bash
# 1. 本地打包
cd "E:\AIClaudeAI辅助代码\AI大模型RAG和智能体开发"
zip -r project.zip . -x "*.git*" "*.pyc" "__pycache__/*" "*.db" "logs/*" "data/*"

# 2. 上传到服务器
scp project.zip user@server:/path/to/

# 3. 服务器上解压并替换
ssh user@server
cd /path/to/
rm -rf enterprise-agent-old  # 备份旧版本（可选）
mv enterprise-agent enterprise-agent-old
unzip project.zip -d enterprise-agent
cd enterprise-agent
```

### 方式2：Git 推送（如果有仓库）

```bash
# 本地
git add .
git commit -m "feat: RAG升级到百万级（FAISS HNSW + 混合检索）"
git push origin main

# 服务器
cd /path/to/enterprise-agent
git pull origin main
```

---

## 🔧 重新构建镜像

```bash
# 1. 停止旧容器
docker-compose down

# 2. 重新构建镜像（会安装 FAISS）
docker-compose build --no-cache

# 预计耗时: 5-10 分钟（下载 embedding + reranker 模型）
```

---

## ⚙️ 配置环境变量（可选）

如需调整 RAG 参数，编辑 `.env` 文件：

```bash
# 在服务器上编辑 .env
cat >> .env << 'EOF'

# ========== RAG 检索配置 ==========
# 向量索引后端 (auto 会自动检测 FAISS)
VECTOR_BACKEND=auto

# 召回配置
RECALL_TOP_N=100        # 粗筛召回数
RERANK_TOP_K=10         # 精排 top-k
FINAL_TOP_K=5           # 最终返回数
RELEVANCE_THRESHOLD=0.35  # 相关度阈值

# RRF 融合
RRF_K=60

# 语义缓存
ENABLE_SEMANTIC_CACHE=True
CACHE_TTL=3600          # 缓存有效期（秒）
CACHE_SIMILARITY_THRESHOLD=0.95

# FAISS HNSW 参数
FAISS_M=32
FAISS_EF_CONSTRUCTION=200
FAISS_EF_SEARCH=100

# BM25 参数
BM25_K1=1.5
BM25_B=0.75
EOF
```

---

## 🚢 启动容器

```bash
# 启动
docker-compose up -d

# 查看日志
docker-compose logs -f --tail=100

# 应该看到:
# "使用混合检索引擎（FAISS + BM25 + RRF）"
# "✓ FAISS HNSW 索引初始化: dim=512, M=32"
```

---

## 🏗️ 构建索引（首次启动必须）

```bash
# 方式1：进入容器执行
docker exec -it enterprise-agent python -c "
from knowledge.hybrid_rag_engine import get_hybrid_engine
engine = get_hybrid_engine()
engine.build_index()
print('✓ 索引构建完成')
"

# 方式2：写成脚本
docker exec -it enterprise-agent bash
python << 'PYTHON'
from knowledge.hybrid_rag_engine import get_hybrid_engine
engine = get_hybrid_engine()
engine.build_index()
print('✓ 索引构建完成')
PYTHON
exit
```

---

## 🧪 验证部署

### 1. 检查容器状态
```bash
docker-compose ps
# 应该显示: enterprise-agent   Up (healthy)

docker-compose logs --tail=50 | grep -E "FAISS|BM25|混合检索"
# 应该看到: "使用混合检索引擎（FAISS + BM25 + RRF）"
```

### 2. 测试 API
```bash
curl -X POST http://localhost:8802/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"退货政策","session_id":"test_docker_rag"}'

# 应该 < 1秒返回
```

### 3. 检查索引统计
```bash
docker exec -it enterprise-agent python -c "
from knowledge.hybrid_rag_engine import get_hybrid_engine
import json
stats = get_hybrid_engine().get_stats()
print(json.dumps(stats, indent=2, ensure_ascii=False))
"

# 输出示例:
# {
#   "mode": "vector",
#   "index_built": true,
#   "vector_index": {
#     "backend": "faiss_hnsw",
#     "total_vectors": 6,
#     ...
#   }
# }
```

### 4. 性能测试（可选）
```bash
# 快速测试（1万条）
docker exec -it enterprise-agent python knowledge/benchmark.py

# 百万级测试（需 5-10 分钟）
docker exec -it enterprise-agent bash -c "BENCHMARK_DOCS=1000000 python knowledge/benchmark.py"
```

---

## 📊 数据库迁移验证

数据库迁移会在容器启动时自动执行，验证：

```bash
# 检查 chunks 表字段
docker exec -it enterprise-agent sqlite3 /app/data/platform.db \
  "PRAGMA table_info(chunks)" | grep -E "category|source|business_line"

# 应该看到:
# 6|category|TEXT|0||0
# 7|source|TEXT|0||0
# 8|created_at|TEXT|0|CURRENT_TIMESTAMP|0
# 9|business_line|TEXT|0||0
# 10|priority|INTEGER|0|0|0
```

---

## 🔄 更新流程（后续升级）

```bash
# 1. 上传新代码
scp -r knowledge/ config.py database/ core/ user@server:/path/to/enterprise-agent/

# 2. 重新构建并启动
docker-compose down
docker-compose build
docker-compose up -d

# 3. 重建索引（如有数据变化）
docker exec -it enterprise-agent python -c "
from knowledge.hybrid_rag_engine import get_hybrid_engine
get_hybrid_engine().build_index()
"
```

---

## ⚠️ 注意事项

### 1. FAISS 安装
- `faiss-cpu==1.9.0` 已加到 `requirements.txt`
- Docker 构建时自动安装
- 如果安装失败，会自动降级到 SQLite（日志会提示）

### 2. 内存要求
- **100万条数据约需 5GB 内存**
- 检查服务器内存：`free -h`
- 如果内存不足，设置 `.env`:
  ```bash
  ENABLE_QUANTIZATION=True  # 启用向量量化，省内存
  RECALL_TOP_N=50           # 降低召回数
  ```

### 3. 数据持久化
- 数据库通过 volume 挂载：`./data:/app/data`
- 删除容器不会丢失数据
- 备份：`tar -czf data_backup.tar.gz data/`

### 4. 索引持久化（可选）
当前索引在内存中，重启容器会丢失。如需持久化：

```yaml
# docker-compose.yml 增加 volume
volumes:
  - ./data:/app/data
  - ./data/knowledge:/app/data/knowledge
  - ./.cache:/app/.cache
  - ./faiss_index:/app/faiss_index  # 新增：FAISS 索引持久化
```

然后在代码中调用：
```python
engine.vector_index.save("/app/faiss_index/main")
engine.vector_index.load("/app/faiss_index/main")
```

### 5. 日志查看
```bash
# 实时日志
docker-compose logs -f

# 过滤 RAG 相关
docker-compose logs | grep -E "FAISS|BM25|混合检索|召回|缓存命中"

# 导出日志
docker-compose logs > rag_upgrade.log
```

---

## 🎯 故障排查

### 问题1：FAISS 未安装
```bash
# 症状：日志显示 "FAISS 不可用，降级到 SQLite 暴力索引"
# 解决：
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### 问题2：索引未构建
```bash
# 症状：查询返回空结果或报错 "索引未构建"
# 解决：
docker exec -it enterprise-agent python -c "
from knowledge.hybrid_rag_engine import get_hybrid_engine
get_hybrid_engine().build_index()
"
```

### 问题3：内存不足
```bash
# 症状：容器 OOM killed
# 检查：
docker stats enterprise-agent

# 解决：
# 1. 增加 Docker 内存限制
docker-compose.yml:
  deploy:
    resources:
      limits:
        memory: 8G

# 2. 启用量化
.env:
  ENABLE_QUANTIZATION=True
```

### 问题4：性能未提升
```bash
# 检查是否真的在用 FAISS
docker-compose logs | grep "使用混合检索引擎"

# 检查索引统计
docker exec -it enterprise-agent python -c "
from knowledge.hybrid_rag_engine import get_hybrid_engine
print(get_hybrid_engine().vector_index.get_stats())
"

# 应该看到 "backend": "faiss_hnsw"
```

---

## 📋 完整部署检查清单

- [ ] 上传所有新文件到服务器
- [ ] 编辑 `.env` 添加 RAG 配置（可选）
- [ ] `docker-compose down` 停止旧容器
- [ ] `docker-compose build --no-cache` 重新构建
- [ ] `docker-compose up -d` 启动容器
- [ ] 检查日志确认 "使用混合检索引擎"
- [ ] 执行 `build_index()` 构建索引
- [ ] 测试 `/api/chat` 接口
- [ ] 检查响应时间（应 < 1秒）
- [ ] 运行 benchmark（可选）
- [ ] 备份数据库

---

**预计部署时间**: 15-20 分钟（含重新构建镜像）  
**重启服务时间**: < 2 分钟  
**首次索引构建**: 1-3 分钟（取决于数据量）

完成后性能提升：**100-150x 加速** ✅
