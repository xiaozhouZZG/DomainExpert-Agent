# 全自动发送测试指南

**实施完成**: ✅ 代码已实现并提交
**测试接口**: `POST /api/admin/test-send`

---

## 实现内容

### 1. 自动选会话（防发错人）

```python
# 根据 target（买家昵称）自动查找并点击会话
conversation_items = page.locator('[class*="conversation-item"]').all()
for item in conversation_items:
    if target in item.text_content():
        item.click()
        break
```

**找不到 target → 直接返回 `{"status": "failed"}`，绝不乱发**

### 2. 稳定选择器（已验证）

- **输入框**: `textarea[placeholder^="请输入消息"]`
- **发送按钮**: `page.get_by_text("发送")` （自动处理空格）
- **自己的消息**: `[class*="message-text-right"]`

### 3. React 受控组件填充

```python
textarea.fill(content)  # 触发 input 事件
assert textarea.input_value() == content  # 校验
```

### 4. 硬校验（最后一条自己的消息）

```python
my_messages = page.locator('[class*="message-text-right"]').all()
last_message = my_messages[-1]

if content in last_message.text_content():
    return {"status": "sent"}  # 硬证据确认
```

**校验逻辑**:
- ✅ **sent**: 最后一条自己的消息包含 content（硬证据）
- ⚠️ **uncertain**: textarea 清空但未找到消息（待人工核查）
- ❌ **failed**: 发送失败或未清空

---

## 测试步骤

### 前提条件

1. ✅ App 在 8802 运行
2. ✅ 浏览器已登录闲鱼
3. ✅ 知道一个测试会话的买家昵称

### 执行测试

#### 方式 1: cURL

```bash
curl -X POST http://localhost:8802/api/admin/test-send \
  -H "Content-Type: application/json" \
  -d '{
    "target": "买家昵称",
    "content": "test"
  }'
```

#### 方式 2: Python

```python
import requests
import json

response = requests.post(
    "http://localhost:8802/api/admin/test-send",
    json={
        "target": "买家昵称",  # 替换为真实昵称
        "content": "test"
    }
)

print(json.dumps(response.json(), indent=2, ensure_ascii=False))
```

#### 方式 3: 浏览器

访问 Swagger UI:
```
http://localhost:8802/docs#/admin/test_send_reply
```

点击 "Try it out"，填写：
```json
{
  "target": "买家昵称",
  "content": "test"
}
```

---

## 预期返回

### 成功（硬证据）

```json
{
  "status": "ok",
  "send_result": {
    "status": "sent",
    "conversation_id": "test_conversation",
    "content": "test",
    "target": "买家昵称",
    "method": "click",
    "evidence": {
      "textarea_cleared": true,
      "message_bubble_found": true,
      "message_text": "test",
      "screenshot": "logs/send_reply_screenshots/send_test_conversation_*.png"
    },
    "detail": "Message sent successfully (hard evidence: found in self bubble)"
  }
}
```

### 不确定（待核查）

```json
{
  "status": "ok",
  "send_result": {
    "status": "uncertain",
    "evidence": {
      "textarea_cleared": true,
      "message_bubble_found": false
    },
    "detail": "Textarea cleared but no self messages found"
  }
}
```

### 失败

```json
{
  "status": "ok",
  "send_result": {
    "status": "failed",
    "detail": "Target conversation not found: '买家昵称'"
  }
}
```

---

## 验证清单

执行测试后，请验证：

1. ✅ **返回的真实 JSON**（完整复制）
2. ✅ **闲鱼界面截图**（显示 "test" 消息）
3. ✅ **消息气泡 outerHTML**（F12 检查你发出的 "test"，Copy outerHTML）
4. ✅ **返回状态是否准确**:
   - `sent`: 消息确实出现
   - `uncertain`: 需要人工核查
   - `failed`: 确实未发送

---

## 需要贴回的信息

1. ✅ **测试命令**（你执行的完整命令）
2. ✅ **真实返回 JSON**（完整的，从终端/浏览器复制）
3. ✅ **闲鱼界面截图**（显示 "test" 消息真的出现）
4. ✅ **消息气泡 outerHTML**（F12 → 右键你发的 "test" → Copy outerHTML）

---

**现在请执行测试，并将真实结果（命令 + JSON + 截图 + outerHTML）贴回。**
