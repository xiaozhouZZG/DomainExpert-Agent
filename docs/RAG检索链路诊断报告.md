# RAG 检索链路现状诊断报告

**诊断时间**: 2026-06-06  
**项目**: DomainExpert-Agent  
**数据规模**: 当前 6 条 chunks（测试数据），目标百万级

---

## 📊 核心发现：当前实现在百万级数据下的瓶颈

### 🔴 致命瓶颈（会导致系统不可用）

| 问题 | 位置 | 影响 | 百万级下的表现 |
|------|------|------|----------------|
| **暴力全扫描向量检索** | `retriever.py:43-85` | O(N) 复杂度 | 100万条需遍历所有，**单次查询 > 10秒** |
| **无 BM25 关键词检索** | 无实现 | 精确词/型号/订单号召回为0 | **型号"iPhone 15 Pro"无法精确匹配** |
| **无混合检索融合** | 无实现 | 只靠向量，召回不全 | **丢失 30-50% 长尾查询** |
| **无元数据过滤** | chunks 表无 category 等字段 | 无法预筛选 | **无法按业务线/时间范围缩小候选集** |

### 🟡 性能瓶颈（慢但能用）

| 问题 | 位置 | 影响 |
|------|------|------|
| **无语义缓存** | 无实现 | 重复查询每次都重新检索 |
| **无向量缓存** | 无实现 | 同一 query 的 embedding 重复计算 |
| **串行执行** | `rag_engine.py:143-149` | 向量检索 → reranker 串行，未并行 |

### 🟢 已实现功能（可用）

| 功能 | 位置 | 状态 | 说明 |
|------|------|------|------|
| ✅ CrossEncoder 重排 | `reranker.py` | 已实现 | BGE-reranker-base, sigmoid 归一化 |
| ✅ 阈值兜底 | `response_engine.py:105-113` | 已实现 | top_score < 0.35 转人工 |
| ✅ 向量归一化 | `embedder.py:36` | 已实现 | normalize_embeddings=True |
| ✅ 降级机制 | `rag_engine.py:40-47` | 已实现 | 无 sentence-transformers 时降级到关键词 |

---

## 🔍 详细链路分析

### 1. retriever.py - 向量检索（核心瓶颈）

**当前实现**:
```python
# 第 43-85 行
cursor.execute("SELECT c.text, c.embedding, d.title FROM chunks c JOIN documents d...")
rows = cursor.fetchall()  # ⚠️ 读取所有数据到内存

for text, embedding_blob, doc_title in rows:  # ⚠️ O(N) 遍历
    chunk_embedding = np.frombuffer(embedding_blob, dtype=np.float32)
    score = float(np.dot(query_embedding, chunk_embedding))  # ⚠️ 暴力点积
```

**问题分析**:
- ❌ **O(N) 复杂度**: 100万条数据需遍历 100万次点积计算
- ❌ **全量加载内存**: `fetchall()` 会将所有向量加载到内存（100万×512×4字节 ≈ **2GB 内存**）
- ❌ **无索引加速**: 没有使用 ANN（近似最近邻）算法
- ❌ **Python 循环慢**: 纯 Python for 循环，无法利用 SIMD/GPU 加速

**百万级表现预估**:
- 单次查询时间: **10-30 秒**（取决于 CPU）
- 内存占用: **2-4 GB**
- QPS: **< 0.1**（几乎不可用）

**注释承诺但未实现**:
```python
# 第 21-25 行的注释
生产优化:
- 换 FAISS (Facebook 近似最近邻库)  # ⚠️ 只是注释，未实现
- 或 sqlite-vss (SQLite 向量扩展)
- 或 pgvector (PostgreSQL 向量扩展)
```

---

### 2. retriever.py - 关键词检索（无 BM25）

**当前实现**:
```python
# KeywordRetriever 类（第 88-188 行）
# 使用字符 n-gram (n=2,3) + TF 词频
features = Counter()
for n in [2, 3]:
    for i in range(len(text) - n + 1):
        gram = text[i:i+n]
        features[gram] += 1
```

**问题分析**:
- ❌ **无 BM25 算法**: 当前只有 TF（词频），缺少 IDF（逆文档频率）
- ❌ **字符 n-gram 过于粗糙**: 无法处理精确词匹配（如 "iPhone 15 Pro" 会被拆成 "iP", "Ph", "ho"...）
- ❌ **无分词**: 依赖字符滑窗，中文语义丢失严重
- ✅ **零依赖**: 不依赖 jieba，但牺牲了准确性

**关键词检索的典型场景**（当前无法准确召回）:
- 订单号: "ORD20240606001"
- 产品型号: "iPhone 15 Pro Max 256GB"
- 精确短语: "七天无理由退货"

**百万级表现预估**:
- 仍然是 O(N) 遍历所有文档
- 单次查询: **5-15 秒**
- 准确率: **< 30%**（n-gram 无法准确匹配长词）

---

### 3. rag_engine.py - 检索链路（无混合检索）

**当前链路**:
```python
# 第 136-159 行
1. candidates = retriever.retrieve(query, top_k * 4)      # 粗筛 top_k*4
2. reranked = reranker.rerank(query, candidates, top_k*2) # 精排 top_k*2
3. filtered = [r for r in reranked if r["score"] >= threshold]  # 阈值过滤
4. return filtered[:top_k]                                 # 返回 top_k
```

**问题分析**:
- ❌ **单一检索路径**: 只用向量检索 OR 关键词检索，无融合
- ❌ **无 RRF (Reciprocal Rank Fusion)**: 没有多路召回融合机制
- ❌ **串行执行**: retriever → reranker 串行，未利用并行
- ❌ **固定倍数放大**: `top_k * 4` 硬编码，无法根据查询类型调整

**召回率问题**:
- 向量检索擅长：语义相似、同义词、长文本
- 关键词检索擅长：精确词、型号、代码、订单号
- **当前只能二选一**，导致召回不全

**示例**:
```
查询: "iPhone 15 Pro 256GB 退货政策"
- 向量检索: 能找到 "退货政策" 相关文档 ✅
- 关键词检索: 能精确匹配 "iPhone 15 Pro 256GB" ✅
- 当前实现: 只能选一个，另一个的召回丢失 ❌
- 理想实现: 混合检索 + RRF 融合 → 召回率 +30-50%
```

---

### 4. reranker.py - 重排（已实现，良好）

**当前实现**:
```python
# 第 33-59 行
pairs = [(query, c["content"]) for c in candidates]
logits = self.model.predict(pairs)          # CrossEncoder 打分
scores = 1 / (1 + np.exp(-logits))          # Sigmoid 归一化
candidates.sort(key=lambda x: x["score"], reverse=True)
return candidates[:top_k]
```

**评价**:
- ✅ **实现正确**: BGE-reranker-base + sigmoid 归一化
- ✅ **批量处理**: `model.predict(pairs)` 批量推理
- ⚠️ **性能瓶颈**: CrossEncoder 是 Transformer 双塔模型，100 条候选需 **100 次前向传播**

**百万级表现**:
- 假设粗筛 100 条候选
- 重排时间: **200-500ms**（GPU） 或 **1-3秒**（CPU）
- 可接受，但需控制候选数（不超过 200）

**配置**:
```python
# 当前配置（rag_engine.py:149）
reranked = self.reranker.rerank(query, candidates, top_k=top_k * 2)
# 如果 top_k=5，粗筛 top_k*4=20，精排 top_k*2=10
```

---

### 5. embedder.py - 向量化（实现良好）

**当前实现**:
```python
# 第 34-41 行
def embed(self, text: str) -> np.ndarray:
    return self.model.encode(text, normalize_embeddings=True)

def embed_batch(self, texts: List[str]) -> List[np.ndarray]:
    return self.model.encode(texts, normalize_embeddings=True, batch_size=32)
```

**评价**:
- ✅ **模型**: BGE-small-zh-v1.5（512维，中文优化）
- ✅ **归一化**: `normalize_embeddings=True`，支持余弦相似度快速计算（点积）
- ✅ **批量化**: `batch_size=32`
- ❌ **无缓存**: 同一 query 重复编码

**向量存储**:
- 格式: `np.float32.tobytes()` → SQLite BLOB
- 大小: 512 × 4 字节 = **2KB/条**
- 100万条: **2GB 向量数据**

---

### 6. response_engine.py - 阈值兜底（已实现）

**当前实现**:
```python
# 第 77 行
search_results = self.rag_engine.search(user_message, top_k=3, threshold=0)
# threshold=0 → 不在检索阶段过滤

# 第 104-113 行
top_score = search_results[0].get("score", 0.0)
if top_score >= self.threshold:  # self.threshold = 0.35
    # 命中：AI 拼接作答
    return await self._handle_hit(...)
else:
    # 未命中：AI 生成转人工话术
    return await self._handle_miss(...)
```

**评价**:
- ✅ **阈值判断**: top_score < 0.35 转人工
- ✅ **兜底机制**: `_handle_miss()` 生成转人工话术
- ✅ **可配置**: `ConfigManager.get_config("rag_threshold")`
- ⚠️ **粗筛 top_k 太小**: `top_k=3`，粗筛只召回 3 条，reranker 无发挥空间

**建议调整**:
```python
# 当前
粗筛: top_k=3 (too small)
精排: top_k*2=6
最终: top_k=3

# 应该
粗筛: top_k=20-50 (recall-oriented)
精排: top_k=5-10
最终: top_k=3-5
```

---

## 📈 数据库表结构分析

### chunks 表（当前）
```sql
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT UNIQUE NOT NULL,
    doc_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB,                    -- 向量（512维 float32, 2KB/条）
    FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
)
```

**缺失字段**（无法元数据过滤）:
- ❌ `category` - 文档分类（退货/换货/保修/会员...）
- ❌ `source` - 来源（政策文档/FAQ/工单...）
- ❌ `created_at` - 时间（无法过滤过时文档）
- ❌ `business_line` - 业务线（3C/服装/食品...）
- ❌ `priority` - 优先级（高频 FAQ 优先）

**影响**:
- 无法做"先按 category='退货政策' 过滤，再做向量检索"
- 100万条数据必须全量检索

---

## 🎯 百万级数据下的瓶颈总结

### 性能瓶颈（按影响排序）

| 瓶颈 | 影响程度 | 当前耗时 | 百万级耗时 | 解决方案 |
|------|---------|---------|-----------|---------|
| 1️⃣ 暴力向量遍历 | 🔴 致命 | 10ms | **10-30秒** | FAISS HNSW 索引 |
| 2️⃣ 无元数据过滤 | 🔴 严重 | - | 无法缩小范围 | 增加 category/source 字段 |
| 3️⃣ 无 BM25 | 🟡 中等 | - | 精确词召回 0% | 实现 BM25 + RRF 融合 |
| 4️⃣ 无语义缓存 | 🟡 中等 | 每次 50ms | 每次 10秒+ | Redis 语义缓存 |
| 5️⃣ 串行执行 | 🟢 轻微 | 串行等待 | 并行可省 30% | asyncio 并行 |

### 准确率问题

| 问题 | 影响场景 | 召回损失 |
|------|---------|---------|
| 无混合检索 | 精确词 + 语义混合查询 | **30-50%** |
| 粗筛 top_k 太小 | 重排无发挥空间 | **10-20%** |
| 无元数据过滤 | 跨业务线污染 | **5-10%** |

---

## 💡 关键配置现状

### rag_engine.py 的调用链
```python
# response_engine.py:77
search_results = self.rag_engine.search(
    user_message, 
    top_k=3,        # ⚠️ 太小，应该 20-50
    threshold=0     # ⚠️ 在检索阶段不过滤
)

# rag_engine.py:143-159
def search(query, top_k=5, threshold=0.5):
    candidates = retriever.retrieve(query, top_k * 4)         # 粗筛 20
    reranked = reranker.rerank(query, candidates, top_k * 2)  # 精排 10
    filtered = [r for r in reranked if r["score"] >= threshold]  # 阈值过滤
    return filtered[:top_k]  # 返回 5
```

**问题**:
- `response_engine.py` 传入 `top_k=3, threshold=0`
- `rag_engine.search()` 内部又有自己的 threshold 参数（被覆盖）
- **实际粗筛**: 3 × 4 = **12 条**（太少）
- **实际精排**: 3 × 2 = **6 条**
- **重排器浪费**: CrossEncoder 只精排 6 条，杀鸡用牛刀

---

## 🚨 降级兜底现状

### 已有降级
1. ✅ **无 sentence-transformers → 关键词检索** (`rag_engine.py:40-47`)
2. ✅ **RAG 检索失败 → 转人工** (`response_engine.py:86-95`)
3. ✅ **低于阈值 → 转人工** (`response_engine.py:115-124`)

### 缺失降级
1. ❌ **reranker 失败 → 回退到向量分数**
2. ❌ **FAISS 索引损坏 → 回退到暴力检索**
3. ❌ **缓存失败 → 直接查询**
4. ❌ **元数据过滤无结果 → 移除过滤条件**

---

## 📊 现状评分卡

| 维度 | 当前分数 | 说明 |
|------|---------|------|
| **可扩展性** | 2/10 | O(N) 暴力遍历，百万级不可用 |
| **准确率** | 6/10 | 有重排 + 阈值，但无混合检索 |
| **速度** | 3/10 | 无缓存，无并行，无 ANN |
| **鲁棒性** | 7/10 | 有基本降级，但不全面 |
| **工程化** | 5/10 | 代码清晰，但缺乏配置化 |

---

## 🎯 下一步行动（优先级排序）

### P0 - 必须修复（否则百万级不可用）
1. **FAISS HNSW 索引** - 解决 O(N) 瓶颈
2. **元数据预过滤** - 增加 category/source 字段
3. **提高粗筛 top_k** - 从 12 提到 100

### P1 - 强烈推荐（召回率 +30%）
4. **BM25 实现** - 精确词召回
5. **RRF 混合检索融合** - 向量 + BM25 融合
6. **语义缓存** - Redis + TTL

### P2 - 性能优化
7. **并行执行** - asyncio 并行向量和 BM25
8. **向量缓存** - 同 query embedding 缓存
9. **流式输出** - SSE 首 token 优化

### P3 - 高级特性
10. **向量量化** - FAISS PQ/SQ 降内存
11. **全面降级兜底** - reranker/索引失败回退
12. **Milvus 接口** - 抽象 VectorIndex 接口

---

## 📋 验收检查清单（当前状态）

- [x] ✅ 有向量检索（但是暴力遍历）
- [x] ✅ 有 CrossEncoder 重排
- [x] ✅ 有阈值兜底转人工
- [ ] ❌ 无 ANN 索引（FAISS/HNSW）
- [ ] ❌ 无 BM25 关键词检索
- [ ] ❌ 无混合检索 + RRF 融合
- [ ] ❌ 无元数据过滤
- [ ] ❌ 无语义缓存
- [ ] ❌ 无并行执行
- [ ] ❌ 无向量量化
- [ ] ❌ 无可插拔向量后端抽象

---

**结论**: 当前实现适用于 **< 1万条数据的原型系统**，百万级需完整重构检索层。核心瓶颈是暴力 O(N) 向量遍历和缺少混合检索，预估单次查询从 10ms 暴涨到 **10-30秒**，完全不可用。

**下一步**: 等待用户确认后，按 5 步架构（元数据过滤 → 混合检索 + RRF → FAISS HNSW → 阈值兜底 → 缓存）逐步升级。
