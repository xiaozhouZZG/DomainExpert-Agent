# 三段式置信度护栏实施报告

**实施时间**: 2026-06-21 22:30
**状态**: ✅ 已完成

---

## 实施内容

### A. 统一检索出口

**文件**: `knowledge/retrieval_gateway.py`

**核心函数**:
1. `get_retrieval_thresholds()` - 从数据库读取阈值配置
2. `search_with_confidence()` - 统一检索出口，三段式置信度判定
3. `format_retrieval_response()` - 格式化响应，决定后续动作

**三段式护栏**:
```python
if top1_score >= high_threshold:  # >= 0.60
    status = 'high'     # 高置信度：可以作答
    action = 'answer'
    
elif top1_score >= low_threshold:  # 0.53 ~ 0.60
    status = 'gray'     # 灰区：转人工（默认）
    action = 'handoff'  # 或 'fallback'（兜底话术）
    
else:  # < 0.53
    status = 'not_found'  # 无可靠答案
    action = 'handoff'
```

---

### B. 阈值配置（可调整）

**存储位置**: 数据库 `system_config` 表

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `retrieval_high_threshold` | 0.60 | 高置信度阈值 (>= 可作答) |
| `retrieval_low_threshold` | 0.53 | 低置信度阈值 (< 无答案) |
| `retrieval_gray_action` | handoff | 灰区动作 (handoff=转人工, fallback=兜底) |

**特点**:
- ✅ **不写死代码** - 存储在数据库，修改无需重启
- ✅ **立即生效** - 每次检索实时读取最新值
- ✅ **附带说明** - 文档明确标注"基于7-chunk小库的临时值"
- ✅ **可追溯** - 每次检索返回当前使用的阈值配置

**修改方法**:
```sql
UPDATE system_config 
SET value = '0.65', updated_at = datetime('now')
WHERE key = 'retrieval_high_threshold';
```

---

### C. 客服护栏接入

#### 1. 知识检索工具 (`tools/knowledge_search.py`)

**修改前**:
```python
results = rag.search(query, top_k=5, threshold=0.5)
return json.dumps({"results": results})
```

**修改后**:
```python
retrieval_result = search_with_confidence(query, top_k=5)

if status == 'high':
    return {"status": "high", "results": [...], 
            "note": "可以作答，但硬事实需人审"}
elif status == 'gray':
    return {"status": "gray", 
            "message": "置信度不足，建议转人工",
            "note": "⚠️ 不要基于低置信度内容作答"}
else:
    return {"status": "not_found",
            "message": "知识库未找到相关信息",
            "note": "必须转人工，不要编造答案"}
```

**客服动作**:
- **high**: LLM可以基于检索结果作答，但：
  - 涉及价格、官方政策、倍率等硬事实 → 仍需人审或固定话术
  - 不让模型自由发挥编造细节

- **gray**: **默认转人工**
  - ❌ 不用"仅供参考"让模型硬答
  - 原因：卖东西场景，错答代价 >> 转人工成本

- **not_found**: **必须转人工**
  - 话术："抱歉，这个问题我没有找到可靠答案，帮您转人工处理"
  - ❌ 绝不让模型编造

---

#### 2. 闲鱼回复生成 (`api/xianyu.py`)

**修改点**: `_strict_reply_rag_search()` 函数

**修改前**:
```python
hit = (top_score >= REPLY_SCORE_THRESHOLD)
return {"hit": hit, "results": results}
```

**修改后**:
```python
# 应用统一阈值
thresholds = get_retrieval_thresholds()

if top_score >= high_threshold:
    confidence_status = 'high'
    hit = True  # 可以使用
elif top_score >= low_threshold:
    confidence_status = 'gray'
    hit = False  # 不使用，转人工
    results = []  # 清空结果
else:
    confidence_status = 'not_found'
    hit = False
    results = []

return {"hit": hit, "confidence_status": confidence_status, ...}
```

**客服动作**:
- `hit=True`: 闲鱼Agent可以使用检索结果生成回复
- `hit=False, confidence_status='gray'`: 灰区，不生成回复，转人工
- `hit=False, confidence_status='not_found'`: 无答案，转人工

---

## 回归测试结果

### 评估指标

**命令**:
```bash
.venv\Scripts\python.exe eval/run_retrieval_eval.py
```

**结果**:
| 指标 | 实施前 | 实施后 | 状态 |
|------|--------|--------|------|
| 正例Recall@5 | 100% | 100% | ✅ 不变 |
| 正例MRR | 0.8667 | 0.8667 | ✅ 不变 |
| 负例拦截率 (阈值0.53) | 93.33% | 93.33% | ✅ 不变 |
| 正例召回率 (阈值0.53) | 93.33% | 93.33% | ✅ 不变 |

**结论**: ✅ **回归测试通过，功能未破坏**

---

### 统一出口测试

**命令**:
```bash
.venv\Scripts\python.exe tests/test_retrieval_gateway.py
```

**测试案例** (6个):

| 问题 | 分数 | 状态 | 动作 | 符合预期 |
|------|------|------|------|---------|
| "键盘大概多少钱" | 0.6971 | high | answer | ✅ |
| "能便宜点吗" | 0.5582 | gray | handoff | ✅ |
| "什么时候发货" | 0.7168 | high | answer | ✅ |
| "支持货到付款吗" | 0.5286 | not_found | handoff | ✅ |
| "能开发票吗" | 0.5005 | not_found | handoff | ✅ |
| "保修几年" | 0.5001 | not_found | handoff | ✅ |

**分布统计**:
- high (可作答): 2/6 (33.3%)
- gray (转人工): 1/6 (16.7%)
- not_found (无答案): 3/6 (50.0%)

**结论**: ✅ **三段式护栏正常工作**

---

## 三段式护栏详解

### High (>= 0.60)

**判定**: 高置信度，检索结果质量好

**允许动作**:
- ✅ LLM 可以基于检索结果作答

**但仍需注意**:
- ⚠️  涉及硬事实（价格、官方政策）→ 人审或固定话术
- ⚠️  不让模型自由发挥细节

**典型案例**:
- "什么时候发货" (分数 0.72)
- "键盘大概多少钱" (分数 0.70)

---

### Gray (0.53 ~ 0.60)

**判定**: 灰区，匹配度一般

**默认动作**: **转人工** (`gray_action = handoff`)

**原因**:
- 卖东西场景：一个错答代价 >> 转人工成本
- 涉及钱的问题，必须保守

**不要做**:
- ❌ 加"仅供参考"让模型硬答
- ❌ 用免责声明降低标准
- ❌ 给模型看灰区的检索结果

**典型案例**:
- "能便宜点吗" (分数 0.56) - 虽然有议价知识，但分数不够高

**可选动作**: 兜底话术 (`gray_action = fallback`)
```
配置: UPDATE system_config SET value = 'fallback' 
      WHERE key = 'retrieval_gray_action';

话术: "这个问题我不太确定，建议您咨询客服获取准确信息"
```

---

### Not Found (< 0.53)

**判定**: 低置信度，知识库没答案

**强制动作**: **转人工**

**话术**: "抱歉，这个问题我没有找到可靠答案，帮您转人工处理"

**典型案例**:
- "支持货到付款吗" (分数 0.53)
- "能开发票吗" (分数 0.50)
- "保修几年" (分数 0.50)

**绝对禁止**: 让模型编造答案

---

## 阈值调整指南

### 当前值来源

基于 `eval/NEGATIVE_EVAL_REPORT.md` 的实验：
- 正例中位分: 0.6650
- 负例中位分: 0.5078
- 重叠区: [0.5115, 0.5483]

### ⚠️ 重要提醒

**这些阈值是基于单商品小库（7个chunks）的临时值！**

**必须重新评估的情况**:
1. 知识库扩充（新增chunks）
2. 知识库内容变化
3. 业务场景变化
4. 用户反馈误判率 > 10%

**重新评估方法**:
```bash
.venv\Scripts\python.exe eval/run_retrieval_eval.py
```

查看**阈值推荐**部分，根据业务需求选择。

---

### 常见场景调整

#### 场景 1: 非交易场景（FAQ）

```sql
-- 可以更宽松
UPDATE system_config SET value = '0.55' WHERE key = 'retrieval_high_threshold';
UPDATE system_config SET value = '0.50' WHERE key = 'retrieval_low_threshold';
UPDATE system_config SET value = 'fallback' WHERE key = 'retrieval_gray_action';
```

#### 场景 2: 电商/金融（当前）

```sql
-- 保守策略
UPDATE system_config SET value = '0.60' WHERE key = 'retrieval_high_threshold';
UPDATE system_config SET value = '0.53' WHERE key = 'retrieval_low_threshold';
UPDATE system_config SET value = 'handoff' WHERE key = 'retrieval_gray_action';
```

#### 场景 3: 法律/医疗（极端保守）

```sql
-- 超保守
UPDATE system_config SET value = '0.70' WHERE key = 'retrieval_high_threshold';
UPDATE system_config SET value = '0.60' WHERE key = 'retrieval_low_threshold';
UPDATE system_config SET value = 'handoff' WHERE key = 'retrieval_gray_action';
```

---

## 文件清单

| 文件 | 作用 |
|------|------|
| `knowledge/retrieval_gateway.py` | ✅ 统一检索出口 |
| `tools/knowledge_search.py` | ✅ 知识检索工具（已接入）|
| `api/xianyu.py` | ✅ 闲鱼回复（已接入）|
| `docs/RETRIEVAL_THRESHOLD_CONFIG.md` | ✅ 配置文档 |
| `tests/test_retrieval_gateway.py` | ✅ 测试脚本 |
| `eval/run_retrieval_eval.py` | ✅ 回归测试脚本 |

---

## 监控和调试

### 查看当前配置

```python
from knowledge.retrieval_gateway import get_retrieval_thresholds

config = get_retrieval_thresholds()
print(config)
# {'high_threshold': 0.6, 'low_threshold': 0.53, 'gray_action': 'handoff'}
```

### 测试检索

```python
from knowledge.retrieval_gateway import search_with_confidence

result = search_with_confidence("键盘大概多少钱")
print(f"Status: {result['status']}")  # high | gray | not_found
print(f"Score: {result['confidence_score']}")
print(f"Action: {result['action']}")  # answer | handoff | fallback
```

### 检索结果自带阈值信息

```json
{
  "status": "gray",
  "confidence_score": 0.56,
  "action": "handoff",
  "threshold_config": {
    "high_threshold": 0.6,
    "low_threshold": 0.53,
    "gray_action": "handoff"
  }
}
```

---

## 核心特点

### ✅ 阈值可配置

- 存储在数据库，不写死代码
- 修改立即生效，无需重启
- 附带文档说明

### ✅ 灰区保守

- 默认转人工，不靠免责声明
- 涉及钱的问题，宁可转人工

### ✅ 回归测试

- 改完必跑评估脚本
- 确保功能未破坏
- 数字与实验一致

### ✅ 统一出口

- 所有检索走同一个护栏
- 置信度判定只此一处
- 便于统一优化

---

## 总结

### 已完成

1. ✅ 统一检索出口 `search_with_confidence()`
2. ✅ 三段式护栏 (high/gray/not_found)
3. ✅ 阈值可配置（数据库 system_config）
4. ✅ 接入知识检索工具
5. ✅ 接入闲鱼回复生成
6. ✅ 回归测试通过
7. ✅ 配置文档完善

### 护栏效果

- 正例召回率: 93.33% (28/30)
- 负例拦截率: 93.33% (14/15)
- 灰区比例: ~13%
- 系统学会说"我没有答案"

### 下一步

1. **监控线上效果**
   - 记录三段分别的触发频率
   - 收集用户反馈

2. **扩充知识库**
   - 针对高频负例补充内容
   - 减少"无答案"情况

3. **动态调整阈值**
   - 根据反馈数据优化
   - 重跑 eval 脚本验证

---

**实施完成。检索系统现在有了"三段式置信度护栏"，能够区分高/中/低置信度，避免基于不可靠信息作答。**
