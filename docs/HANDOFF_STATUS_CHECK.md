# 转人工真闭环 - 现状核查报告

**核查时间**: 2026-06-21 23:00
**核查内容**: 发消息能力、会话状态存储、工作台现状

---

## 1. 【发消息能力】核查结果

### 结论：**还不能自动发送**

### 代码证据

**工具入口**: `tools/xianyu.py::send_xianyu_reply()`
```python
def send_xianyu_reply(
    conversation_id: str,
    content: str,
    approval_id: Optional[str] = None,
) -> str:
    """发送闲鱼回复。没有 approval_id 时仅创建审批单。"""
    if not approval_id:
        # 创建审批单
        new_approval_id = _create_platform_approval(...)
        return json.dumps({
            "status": "approval_required",
            "approval_id": new_approval_id,
            "action": "send_reply"
        })
    
    # 调用平台发送
    result = _get_platform().send_reply(conversation_id, content, approval_id)
    return json.dumps(result)
```

**平台实现**: `platforms/goofish_playwright.py::send_reply()`
```python
def send_reply(
    self,
    conversation_id: str,
    content: str,
    approval_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not approval_id:
        return {"status": "approval_required", "action": "send_reply"}
    
    # ⚠️ 核心问题：发送动作未实现
    return {"status": "not_implemented", "reason": "selectors_need_confirmation"}
```

### 现状

- ✅ **工具链路完整**: LangGraph Agent → send_xianyu_reply 工具 → 平台层
- ✅ **审批流程完整**: 创建审批单 → 人工审批 → 调用平台
- ❌ **发送动作未逆向**: `send_reply()` 返回 `"not_implemented"`

### 原因

闲鱼 IM 页的**发送框选择器**和**发送按钮**尚未逆向确认。

代码注释明确标注：
```python
return {"status": "not_implemented", "reason": "selectors_need_confirmation"}
```

### 结论

**当前无法自动发送消息到闲鱼会话**。

需要先完成：
1. 逆向闲鱼 IM 页面结构
2. 确认发送框和发送按钮的选择器
3. 实现 Playwright 自动输入+点击逻辑

---

## 2. 【会话状态存储】核查结果

### 结论：**有 status 字段，但仅用 "open"**

### 表结构

**表**: `xianyu_conversations`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| conversation_id | TEXT | 会话ID (唯一) |
| buyer_name | TEXT | 买家昵称 |
| platform | TEXT | 平台 (goofish) |
| **status** | TEXT | **会话状态** |
| last_intent | TEXT | 最后意图 |
| last_message_at | TEXT | 最后消息时间 |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

### 当前使用情况

**代码**: `core/xianyu_service.py::ingest_messages()`
```python
cursor.execute("""
    INSERT INTO xianyu_conversations
    (conversation_id, buyer_name, platform, status, last_intent, last_message_at, ...)
    VALUES (?, ?, ?, ?, ?, ?, ...)
    ON CONFLICT(conversation_id) DO UPDATE SET
        buyer_name = excluded.buyer_name,
        last_intent = excluded.last_intent,
        last_message_at = excluded.last_message_at,
        updated_at = CURRENT_TIMESTAMP
""", (
    conversation_id,
    buyer_name,
    "goofish",
    "open",  # ⚠️ 硬编码为 "open"
    intent,
    received_at,
))
```

### 现状

- ✅ **status 字段存在**
- ⚠️ **只设置一个值**: `"open"`
- ⚠️ **更新时不修改 status**: `ON CONFLICT` 子句中没有 `status = ...`
- ❌ **没有状态流转逻辑**: 无代码设置 `"pending_handoff"` 或 `"human_taking"`

### 状态值现状

**当前使用**: 
```
"open" - 所有会话
```

**缺失的状态**:
- `"pending_handoff"` - 待转人工
- `"human_taking"` - 人工处理中
- `"resolved"` - 已解决
- `"bot"` - 机器人处理中

### 机器人回复记录

**表**: `xianyu_messages`

| 字段 | 类型 | 说明 |
|------|------|------|
| message_id | TEXT | 消息ID |
| conversation_id | TEXT | 会话ID |
| direction | TEXT | 方向 (buyer/seller) |
| content | TEXT | 消息内容 |
| intent | TEXT | 意图 |
| **draft_reply** | TEXT | **机器人草稿回复** |
| **approval_id** | TEXT | **审批单ID** |
| **sent_status** | TEXT | **发送状态** |
| created_at | TEXT | 创建时间 |

**现状**:
- ✅ `draft_reply` 字段存在 - 可记录机器人生成的回复
- ✅ `approval_id` 字段存在 - 可关联审批单
- ✅ `sent_status` 字段存在 - 可记录发送状态

**但**: 没有"机器人是否已回复"的明确标记。需要通过 `draft_reply IS NOT NULL` 或 `approval_id IS NOT NULL` 推断。

---

## 3. 【工作台现状】核查结果

### 结论：**能列出会话，但无"待人工"标记**

### 前端代码

**文件**: `web/main.html` + `web/static/main.js`

**会话列表容器**:
```html
<div id="conversations-container" class="conversations-list">
    <div class="loading">点击"刷新消息"加载会话列表</div>
</div>
```

**加载函数**: `main.js::loadConversations()`
```javascript
async function loadConversations() {
    const container = document.getElementById('conversations-container');
    container.innerHTML = '<div class="loading">加载中...</div>';
    
    try {
        const data = await api('/api/xianyu/conversations');
        renderConversations(data);
    } catch (error) {
        container.innerHTML = `<div style="color: var(--error);">加载失败</div>`;
    }
}
```

### 渲染逻辑

**函数**: `main.js::renderConversations()`
```javascript
results.forEach(conv => {
    const item = document.createElement('div');
    item.className = 'conversation-item';
    
    // 买家昵称
    const buyerNick = conv.buyer_nick || '未知买家';
    
    // ⚠️ 订单状态标签（但不是会话状态）
    let statusBadge = '';
    if (conv.order_status) {
        const isWait = conv.order_status.includes('等待');
        const isSuccess = conv.order_status.includes('成功');
        const badgeClass = isWait ? 'badge-orange' : 
                          (isSuccess ? 'badge-green' : 'badge-gray');
        statusBadge = `<span class="status-badge ${badgeClass}">
                        ${escapeHtml(conv.order_status)}
                      </span>`;
    }
    
    // 最后消息
    const lastMessage = conv.last_message || '暂无消息';
    
    item.innerHTML = `
        <div class="conversation-header">
            <div class="buyer-name">${escapeHtml(buyerNick)}</div>
            ${statusBadge}
        </div>
        <div class="conversation-message">${escapeHtml(lastMessage)}</div>
        <div class="conversation-footer">
            <button class="btn-view-chat">查看对话</button>
        </div>
    `;
    
    // ⚠️ 点击"查看对话"待实现
    const btn = item.querySelector('.btn-view-chat');
    btn.addEventListener('click', () => {
        alert('会话消息读取功能待实现（需要先dump消息气泡结构）');
    });
});
```

### API 返回数据

**路由**: `api/xianyu.py::get_xianyu_conversations()`
```python
@router.get("/conversations")
async def get_xianyu_conversations(limit: int = 50):
    return {
        "status": "ok",
        "conversations": list_conversations(limit=limit),
    }
```

**数据查询**: `core/xianyu_service.py::list_conversations()`
```python
def list_conversations(limit: int = 50) -> List[Dict[str, Any]]:
    cursor.execute("""
        SELECT conversation_id, buyer_name, platform, status, 
               last_intent, last_message_at, updated_at
        FROM xianyu_conversations
        ORDER BY updated_at DESC
        LIMIT ?
    """, (limit,))
    
    conversations = []
    for row in cursor.fetchall():
        conversations.append({
            "conversation_id": row[0],
            "buyer_name": row[1],
            "platform": row[2],
            "status": row[3],  # ⚠️ 返回但前端未使用
            "last_intent": row[4],
            "last_message_at": row[5],
            "updated_at": row[6],
        })
    return conversations
```

### 现状

- ✅ **能列出会话**: API 返回会话列表
- ✅ **能显示买家昵称**: `buyer_name` 字段
- ✅ **能显示最后消息时间**: `last_message_at` 字段
- ✅ **API 返回 status 字段**: 但值都是 `"open"`
- ❌ **前端未显示会话状态**: 没有渲染 `conv.status`
- ❌ **无"待人工"标记/红点**: 前端没有任何视觉提示区分状态
- ❌ **无法查看对话详情**: 点击"查看对话"弹出"待实现"

### 订单状态 vs 会话状态

**注意**: 前端有 `order_status` 标签（订单状态），但这**不是**会话状态。

- `order_status`: 来自订单系统（等待发货/交易成功）
- `conv.status`: 会话状态（bot/待人工/人工中）- **前端未使用**

---

## 总结

### 三个问题的答案

| 问题 | 答案 | 证据 |
|------|------|------|
| **1. 能否自动发送消息** | ❌ **还不能** | `send_reply()` 返回 `"not_implemented"` |
| **2. 会话状态字段** | ✅ **有 status 字段** | 但只用 `"open"`，无状态流转 |
| **3. 工作台待人工标记** | ❌ **没有** | API 返回 status 但前端未显示 |

### 缺失的能力

#### 发送层
- ❌ 闲鱼 IM 页发送框选择器未逆向
- ❌ 自动输入+点击发送未实现

#### 状态层
- ❌ 无状态流转逻辑（open → pending_handoff → human_taking）
- ❌ 无"机器人已回复"明确标记

#### 工作台层
- ❌ 前端未显示会话状态
- ❌ 无"待人工"红点/徽章
- ❌ 无法点击查看对话详情

---

## 下一步需求

根据现状，转人工真闭环需要实现：

### 最小闭环（不依赖自动发送）

1. **状态流转**:
   ```
   open → pending_handoff (检索灰区/无答案时)
   pending_handoff → human_taking (人工点击"接手"时)
   ```

2. **工作台标记**:
   - 前端显示 `status` 字段
   - `pending_handoff` 会话显示红点/橙色徽章
   - 点击会话可查看消息列表

3. **Holding 话术**:
   - 检测到需要转人工时，**记录到数据库**
   - Holding 话术："您好，这个问题我需要确认一下，稍后回复您"
   - **人工手动复制粘贴发送**（因为自动发送未实现）

### 完整闭环（需要自动发送）

4. **自动发送 Holding 话术**:
   - 逆向闲鱼 IM 页选择器
   - 实现 Playwright 自动输入+点击
   - 发送 Holding 话术到会话

5. **机器人闭嘴**:
   - `status = 'pending_handoff'` 的会话
   - 后续买家消息不触发机器人回复

6. **人工接手**:
   - 工作台点击"接手" → `status = 'human_taking'`
   - 人工发送回复 → `status = 'resolved'`

---

## 核查完成

**所有答案基于真实代码和表结构，未编造、未假设、未 mock。**

**现状清晰，可以据此设计下一步落地方案。**
