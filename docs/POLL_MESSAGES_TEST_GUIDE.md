# 消息轮询读取器测试指南

**实施时间**: 2026-06-22
**模式**: 影子模式（只读不发）

---

## 实现内容

### 核心功能

#### 1. poll_unread_conversations()

**位置**: `platforms/goofish_playwright.py`

**功能**:
- 遍历会话列表 `[class*="conversation-item"]`
- 检查未读角标 `.ant-badge-count`
- 系统号黑名单过滤
- 点进会话读取最后一条消息方向

#### 2. 判断 needs_reply

```python
# 最后一条消息气泡
last_bubble_html = last_bubble.evaluate("el => el.outerHTML")

if "message-text-left" in last_bubble_html:
    # 买家在等回复
    needs_reply = True
    
elif "message-text-right" in last_bubble_html:
    # 已回复
    needs_reply = False
```

#### 3. 系统号黑名单（宁漏勿杀）

```python
SYSTEM_ACCOUNT_BLACKLIST = [
    "通知消息",
    "官方代充",
    "闲鱼小蜜",
    "系统消息",
    "平台通知",
]
```

### 返回结构

```json
{
  "status": "ok",
  "conversations": [
    {
      "buyer_name": "海王星上蹿下跳的豆浆",
      "last_buyer_msg": "还能便宜点吗",
      "unread_count": 2,
      "needs_reply": true
    },
    {
      "buyer_name": "另一个买家",
      "last_buyer_msg": null,
      "unread_count": 1,
      "needs_reply": false
    }
  ],
  "total": 2,
  "needs_reply_count": 1
}
```

---

## API 接口

**端点**: `GET /api/admin/poll-messages`

**特性**:
- ✅ 只读不发（物理隔离，不调用 send_reply）
- ✅ 复用常驻浏览器（browser_worker）
- ✅ 系统号过滤
- ✅ 判断是否需要回复

---

## 测试步骤

### 方式 1: cURL

```bash
curl -X GET http://localhost:8802/api/admin/poll-messages
```

### 方式 2: 浏览器

直接访问：
```
http://localhost:8802/api/admin/poll-messages
```

### 方式 3: Python

```python
import requests
import json

response = requests.get("http://localhost:8802/api/admin/poll-messages")
print(json.dumps(response.json(), indent=2, ensure_ascii=False))
```

---

## 预期结果

### 场景 1: 有未读且需要回复

```json
{
  "status": "ok",
  "conversations": [
    {
      "buyer_name": "买家A",
      "last_buyer_msg": "这个键盘多少钱",
      "unread_count": 1,
      "needs_reply": true
    }
  ],
  "total": 1,
  "needs_reply_count": 1
}
```

### 场景 2: 有未读但已回复

```json
{
  "status": "ok",
  "conversations": [
    {
      "buyer_name": "买家B",
      "last_buyer_msg": null,
      "unread_count": 1,
      "needs_reply": false
    }
  ],
  "total": 1,
  "needs_reply_count": 0
}
```

### 场景 3: 无未读消息

```json
{
  "status": "ok",
  "conversations": [],
  "total": 0,
  "needs_reply_count": 0
}
```

### 场景 4: 系统号被过滤

```json
{
  "status": "ok",
  "conversations": [
    // 不包含 "通知消息"、"官方代充" 等系统号
  ],
  "total": 0,
  "needs_reply_count": 0
}
```

---

## 验证清单

执行测试后，请验证：

1. ✅ **返回的真实 JSON**（完整复制）
2. ✅ **conversations 数组内容**
3. ✅ **needs_reply 判断是否准确**:
   - 最后一条是买家消息 → needs_reply=true
   - 最后一条是自己消息 → needs_reply=false
4. ✅ **系统号是否被过滤**
5. ✅ **未读数是否正确**
6. ✅ **没有发送任何消息**（影子模式验证）

---

## 需要贴回的信息

1. ✅ **测试命令**（你执行的完整命令）
2. ✅ **真实返回 JSON**（完整的，从终端/浏览器复制）
3. ✅ **闲鱼界面截图**（显示哪些会话有未读）
4. ✅ **验证结果**:
   - needs_reply 判断是否准确
   - 系统号是否被过滤
   - 是否真的没有发送消息

---

## 注意事项

### ⚠️ 影子模式

- **绝对不会发送消息**
- 只读取、记录、返回
- 可以安全地在真实账号上运行

### ⚠️ 系统号过滤

- 采用"宁漏勿杀"策略
- 只过滤明确的系统号
- 如果漏过系统号，无害（只读场景）

### ⚠️ 执行时间

- 需要遍历所有未读会话
- 每个会话点进去读取消息
- 可能需要 30-60 秒

---

**请执行测试，并将真实结果（命令 + JSON + 截图 + 验证结果）贴回。**
