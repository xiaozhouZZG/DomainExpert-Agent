# 检索评估 Baseline 报告

**评估时间**: 2026-06-21 21:40
**状态**: ✅ Baseline 已建立

---

## 评估集构建

### 数据来源

**真实知识库内容**:
- 来源: 闲鱼回复种子知识库 (`xianyu_reply_seed_kb_20260101`)
- Chunks 总数: 7 个
- 分类: product_info, negotiation, shipping, condition, market_sample

### 评估集规模

- **总案例数**: 30 条
- **问题类型**: 7 个分类
  - price (价格): 5 个案例
  - negotiation (议价): 4 个案例
  - shipping (发货): 6 个案例
  - product_info (产品信息): 5 个案例
  - condition (成色): 6 个案例
  - market (市场): 3 个案例
  - listing (刊登): 1 个案例

### 评估集样例

#### 案例 1: 价格查询
```json
{
  "question": "键盘大概多少钱",
  "expected_chunk_ids": ["xianyu-reply-seed-keyboard_chunk_0_0"],
  "category": "price"
}
```

**检索结果**:
1. [ ] xianyu-reply-seed-keyboard_chunk_2_0 (分数: 0.6971) - 议价相关
2. [✓] xianyu-reply-seed-keyboard_chunk_0_0 (分数: 0.6837) - **期望chunk**（命中）
3. [ ] xianyu-reply-seed-keyboard_chunk_5_0 (分数: 0.6807) - 竞品样本

**结果**: ✓ 命中，排名第2

---

#### 案例 4: 议价
```json
{
  "question": "能便宜点吗",
  "expected_chunk_ids": ["xianyu-reply-seed-keyboard_chunk_2_0"],
  "category": "negotiation"
}
```

**检索结果**:
1. [✓] xianyu-reply-seed-keyboard_chunk_2_0 (分数: 0.5582) - **期望chunk**（命中）
2. [ ] xianyu-reply-seed-keyboard_chunk_5_0 (分数: 0.5139) - 竞品样本
3. [ ] xianyu-reply-seed-keyboard_chunk_0_0 (分数: 0.5093) - 价格区间

**结果**: ✓ 命中，排名第1

---

#### 案例 3: 市场价（多个期望答案）
```json
{
  "question": "键盘市场价多少",
  "expected_chunk_ids": [
    "xianyu-reply-seed-keyboard_chunk_0_0",
    "xianyu-reply-seed-keyboard_chunk_5_0"
  ],
  "category": "price"
}
```

**检索结果**:
1. [✓] xianyu-reply-seed-keyboard_chunk_0_0 (分数: 0.7251) - **期望chunk 1**
2. [ ] xianyu-reply-seed-keyboard_chunk_2_0 (分数: 0.7231) - 议价相关
3. [✓] xianyu-reply-seed-keyboard_chunk_5_0 (分数: 0.6876) - **期望chunk 2**

**结果**: ✓ 两个期望chunk都在top-5，排名1和3

---

## Baseline 性能指标

### 总体统计

#### Top-5 结果
- **总案例数**: 30
- **命中案例数**: 30
- **命中率 (Hit Rate)**: **100.00%**
- **平均 Recall@5**: **100.00%**
- **MRR (Mean Reciprocal Rank)**: **0.8667**
- **平均命中排名**: **1.27**

#### Top-10 结果
- **总案例数**: 30
- **命中案例数**: 30
- **命中率 (Hit Rate)**: **100.00%**
- **平均 Recall@10**: **100.00%**
- **MRR (Mean Reciprocal Rank)**: **0.8667**
- **平均命中排名**: **1.27**

### 分类统计 (Recall@5)

| 分类 | Recall@5 | 案例数 |
|------|----------|--------|
| condition (成色) | 100.00% | 6 |
| listing (刊登) | 100.00% | 1 |
| market (市场) | 100.00% | 3 |
| negotiation (议价) | 100.00% | 4 |
| price (价格) | 100.00% | 5 |
| product_info (产品) | 100.00% | 5 |
| shipping (发货) | 100.00% | 6 |

**所有分类 100% 命中！**

---

## 关键发现

### ✅ 优点

1. **100% 命中率**
   - 所有30个案例都能在 top-5 中找到期望的chunk
   - 说明当前检索系统对键盘相关问题覆盖完整

2. **排名靠前**
   - MRR = 0.8667，非常高
   - 平均命中排名 1.27，说明期望chunk通常在第1或第2位
   - 用户几乎总能在前2个结果中找到答案

3. **跨分类稳定**
   - 7个不同分类的问题都能准确检索
   - 价格、议价、发货、产品信息、成色等场景全覆盖

### ⚠️ 观察到的问题

#### 1. 排名不够理想的案例

**案例 1: "键盘大概多少钱"**
- 期望chunk排第2（分数0.6837）
- 第1名是议价chunk（分数0.6971）
- **原因**: 议价chunk中也提到了价格区间，语义相近

**案例 2: "这个键盘啥价位"**
- 期望chunk排第2（分数0.6378）
- 第1名是议价chunk（分数0.6553）
- **原因**: 同上

**观察**: 价格相关问题时，议价chunk经常排在价格chunk前面，因为议价内容也包含价格信息。

#### 2. 分数区分度不够

多个案例中，top-3的分数非常接近：
- 案例1: 0.6971 vs 0.6837 (差0.0134)
- 案例3: 0.7251 vs 0.7231 (差0.0020)

**影响**: 虽然命中率100%，但排名不够稳定，top-1有时不是最佳答案。

#### 3. 知识库规模小

- 只有7个chunks
- 所有问题都基于这7个chunks
- 无法评估"找不到答案"的场景

---

## 检索系统配置

### 当前配置

**引擎**: `get_hybrid_engine()`
- 模式: vector + BM25 混合检索
- 向量模型: BGE-small-zh-v1.5
- 重排模型: BGE-reranker-base
- Top-K: 5 (常用), 10 (评估用)

### 检索流程

1. **向量检索**: BGE向量化 + FAISS索引
2. **BM25检索**: 基于词频的传统检索
3. **混合融合**: Reciprocal Rank Fusion (RRF)
4. **重排**: BGE reranker重新排序

---

## Baseline 结论

### 当前性能

✅ **非常好的 Baseline**:
- Recall@5: 100%
- MRR: 0.8667
- 平均排名: 1.27

### 改进空间

虽然命中率100%，但仍有优化空间：

1. **提升排名准确性**
   - 目标: 将MRR从0.8667提升到0.90+
   - 方法: 优化混合权重、重排策略

2. **提升分数区分度**
   - 目标: top-1和top-2的分数差拉大到0.05+
   - 方法: 调整BM25/向量权重、改进重排模型

3. **扩充评估集**
   - 目标: 增加到100+案例，覆盖更多场景
   - 包含"找不到答案"的负例

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `eval/retrieval_eval.jsonl` | 评估集（30个案例，真实chunk_id） |
| `eval/run_retrieval_eval.py` | 评估脚本（Recall@k, MRR） |
| `eval/retrieval_eval_results.json` | 详细评估结果（JSON格式） |
| `eval_output.txt` | 完整输出日志（67KB） |

---

## 下一步计划

### 第一步：理解当前检索逻辑（已完成）
- ✅ 建立评估尺子
- ✅ 量出当前baseline

### 第二步：检索加厚实验
1. **调整混合权重**
   - 实验不同的向量/BM25权重比例
   - 每次调整后重新评估

2. **优化重排策略**
   - 尝试不同的重排模型参数
   - 实验不同的top-k for rerank

3. **Query扩展**
   - 同义词扩展
   - 关键词提取

4. **负例测试**
   - 添加"找不到答案"的问题
   - 测试系统是否会误返回低相关结果

---

## 使用方法

### 运行评估

```bash
cd E:\AIClaudeAI辅助代码\AI大模型RAG和智能体开发
.venv\Scripts\python.exe eval/run_retrieval_eval.py
```

### 输出

- 终端: 逐条打印 + 总体统计
- 文件: `eval/retrieval_eval_results.json`

### 添加新案例

编辑 `eval/retrieval_eval.jsonl`，每行一个JSON:

```json
{"question": "新问题", "expected_chunk_ids": ["chunk_id_1"], "category": "category_name"}
```

---

**Baseline 已建立。检索系统当前表现优秀（Recall@5 100%），但仍有提升排名准确性的空间。**
