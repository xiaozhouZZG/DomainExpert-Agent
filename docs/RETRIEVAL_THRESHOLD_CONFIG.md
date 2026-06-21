# 检索阈值配置文档

## 配置位置

**数据库**: `system_config` 表

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `retrieval_high_threshold` | 0.60 | 高置信度阈值。分数 >= 此值：可直接作答 |
| `retrieval_low_threshold` | 0.53 | 低置信度阈值。分数 < 此值：无可靠答案 |
| `retrieval_gray_action` | handoff | 灰区动作。`handoff`=转人工，`fallback`=兜底话术 |

## 阈值说明

### 当前值来源

基于 `eval/NEGATIVE_EVAL_REPORT.md` 的实验结果：

- **正例中位分**: 0.6650（有答案的问题）
- **负例中位分**: 0.5078（没答案的问题）
- **分数重叠区**: [0.5115, 0.5483]

**阈值选择**:
- `high_threshold = 0.60`: 高于正例中位数，确保高质量
- `low_threshold = 0.53`: 在重叠区上方，平衡召回和精确
  - 召回率: 93.33% (30个正例中28个)
  - 拦截率: 93.33% (15个负例中14个)

### ⚠️ 重要提醒

**这些阈值是基于单商品小库（7个chunks）的临时值！**

当以下情况发生时，**必须重新评估**：
1. 知识库扩充（新增chunks）
2. 知识库内容变化
3. 业务场景变化
4. 用户反馈发现大量误判

**重新评估方法**：
```bash
cd E:\AIClaudeAI辅助代码\AI大模型RAG和智能体开发
.venv\Scripts\python.exe eval/run_retrieval_eval.py
```

查看报告中的**阈值推荐**部分，根据业务需求选择合适阈值。

---

## 三段式护栏

### High (>= 0.60)

**判定**: 高置信度，匹配度很高

**动作**: 可以让 LLM 作答

**但是**：
- 涉及硬事实（价格、官方政策、倍率）仍需人审或固定话术
- 不让模型自由发挥编造细节

**典型问题**:
- "能便宜点吗" → 议价话术 (分数 0.66)
- "什么时候发货" → 发货时效 (分数 0.68)

---

### Gray (0.53 ~ 0.60)

**判定**: 灰区，匹配度一般

**默认动作**: **转人工** (`gray_action = handoff`)

**原因**: 卖东西场景下，一个自信的错答代价 >> 转人工成本

**不要做的事**:
- ❌ 加"仅供参考"让模型硬答
- ❌ 用免责声明降低标准

**典型问题**:
- "键盘大概多少钱" → 价格信息 (分数 0.56，但议价chunk排第一)
- 边缘案例

**可选动作**: 兜底话术 (`gray_action = fallback`)
- 适用于非关键场景
- "这个问题我不太确定，建议您咨询客服获取准确信息"

---

### Not Found (< 0.53)

**判定**: 低置信度，知识库没有可靠答案

**动作**: 转人工 + 明确告知

**话术**: "抱歉，这个问题我没有找到可靠答案，帮您转人工处理"

**典型问题**:
- "支持货到付款吗" (分数 0.50)
- "能开发票吗" (分数 0.50)
- "保修几年" (分数 0.50)

**绝对不做**: 让模型编造答案

---

## 修改配置

### 方法 1: 通过 API

```bash
curl -X POST http://127.0.0.1:8802/api/admin/system-config \
  -H "Content-Type: application/json" \
  -d '{
    "key": "retrieval_high_threshold",
    "value": "0.65"
  }'
```

### 方法 2: 直接修改数据库

```sql
UPDATE system_config 
SET value = '0.65', updated_at = datetime('now')
WHERE key = 'retrieval_high_threshold';
```

### 方法 3: 使用 Python 脚本

```python
import sqlite3
from datetime import datetime

conn = sqlite3.connect('data/platform.db')
cursor = conn.cursor()

cursor.execute('''
    UPDATE system_config 
    SET value = ?, updated_at = ?
    WHERE key = ?
''', ('0.65', datetime.now().isoformat(), 'retrieval_high_threshold'))

conn.commit()
conn.close()
```

---

## 配置生效

配置修改后**立即生效**（下次检索请求生效）。

`get_retrieval_thresholds()` 每次检索时从数据库读取最新值。

---

## 调试和监控

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
print(f"Status: {result['status']}")
print(f"Score: {result['confidence_score']}")
print(f"Action: {result['action']}")
```

### 检索结果包含阈值信息

每次检索返回的 `threshold_config` 字段记录了当前使用的阈值：

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

## 常见场景阈值调整

### 场景 1: 宁可多答，少转人工

适用于：非交易场景、FAQ

```
high_threshold: 0.55 ↓
low_threshold: 0.50 ↓
gray_action: answer (让模型答，加"仅供参考")
```

### 场景 2: 宁可转人工，不要错答（当前）

适用于：电商、金融、医疗

```
high_threshold: 0.60 (当前值)
low_threshold: 0.53 (当前值)
gray_action: handoff (当前值)
```

### 场景 3: 极端保守

适用于：法律、医疗诊断

```
high_threshold: 0.70 ↑
low_threshold: 0.60 ↑
gray_action: handoff
```

---

## 评估指标目标

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| 正例召回率 | 93.33% | >= 95% |
| 负例拦截率 | 93.33% | >= 90% |
| 灰区比例 | ~13% | < 20% |

**优化方向**:
1. 扩充知识库 → 减少负例
2. 改进chunk质量 → 提升分数区分度
3. 训练重排模型 → 拉大正例/负例分数差距

---

## 版本历史

| 版本 | 日期 | 阈值 | 说明 |
|------|------|------|------|
| 1.0 | 2026-06-21 | 0.53/0.60 | 初始版本，基于7-chunk评估 |

---

**下次更新时机**: 知识库chunks数量翻倍或业务反馈误判率 > 10%
