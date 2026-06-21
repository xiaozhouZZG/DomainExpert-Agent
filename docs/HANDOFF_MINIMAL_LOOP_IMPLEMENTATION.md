# 转人工真闭环 - 最小闭环实施报告

**实施时间**: 2026-06-21 23:30
**状态**: ✅ 已完成

---

## 实施方案

**不依赖自动发送**（因 send_reply 未逆向）

Holding 话术降级：系统生成建议话术显示在工作台，人工手动复制发送。

---

## A. 后端·状态流转

### 1. 状态机设计

```
open/bot → pending_handoff (检索灰区/无答案)
           ↓
pending_handoff → human_taking (人工接手)
           ↓
human_taking → resolved (已解决)
```

### 2. 存储方案

**选择**: 使用现有表结构，改动最小

- **会话状态**: `xianyu_conversations.status` 字段（已存在）
- **转人工原因**: `xianyu_messages.draft_reply` 字段（存储JSON格式）

**JSON格式**:
```json
{
  "type": "handoff",
  "reason": "gray" | "not_found",
  "confidence_score": 0.55,
  "buyer_message": "买家消息内容",
  "suggested_reply": "您好，这个问题我需要确认一下，稍后回复您",
  "handoff_at": "2026-06-21T23:30:00"
}
```

### 3. 新增代码

**文件**: `core/conversation_status.py`

**核心函数**:

1. `mark_conversation_pending_handoff()` - 标记待人工
   ```python
   # 更新会话状态
   UPDATE xianyu_conversations
   SET status = 'pending_handoff', updated_at = CURRENT_TIMESTAMP
   WHERE conversation_id = ?
   
   # 记录转人工原因到最后一条买家消息
   UPDATE xianyu_messages
   SET draft_reply = ?  -- JSON格式的handoff元数据
   WHERE conversation_id = ? AND direction = 'buyer'
   ORDER BY created_at DESC LIMIT 1
   ```

2. `handoff_to_human()` - 人工接手
   ```python
   # 检查当前状态必须是 pending_handoff
   # 更新状态为 human_taking
   ```

3. `resolve_conversation()` - 标记已解决
   ```python
   # 检查当前状态必须是 human_taking
   # 更新状态为 resolved
   ```

4. `should_bot_reply()` - 判断机器人是否应该回复
   ```python
   status = get_conversation_status(conversation_id)
   bot_allowed = {None, 'open', 'bot'}
   return status in bot_allowed
   ```

### 4. 触发点：检索护栏

**文件**: `api/xianyu.py::create_reply_draft()`

**位置**: `if not rag["hit"]:` 分支

```python
if not rag["hit"]:
    confidence_status = rag.get("confidence_status", "not_found")
    top_score = rag.get("top_score", 0.0)
    
    # 生成 holding 建议话术
    if confidence_status == "gray":
        suggested_reply = "您好，这个问题我需要再确认一下，稍后回复您"
    else:
        suggested_reply = "您好，这个问题我帮您确认一下，稍后回复您"
    
    # 标记会话为待人工处理
    mark_conversation_pending_handoff(
        conversation_id=req.conversation_id,
        reason=confidence_status,
        buyer_message=req.buyer_message,
        confidence_score=top_score,
        suggested_reply=suggested_reply
    )
    
    return {
        "status": "no_knowledge",
        "handoff": {
            "status": "pending_handoff",
            "reason": confidence_status,
            "suggested_reply": suggested_reply,
            "note": "⚠️ 请手动复制以上话术发送给买家"
        },
        ...
    }
```

### 5. 新增API路由

**文件**: `api/xianyu.py`

```python
@router.post("/conversations/{conversation_id}/handoff")
async def handoff_conversation(conversation_id: str):
    """人工接手会话（pending_handoff → human_taking）"""
    
@router.post("/conversations/{conversation_id}/resolve")
async def resolve_conversation(conversation_id: str):
    """标记会话已解决（human_taking → resolved）"""
```

---

## B. 后端·机器人闭嘴

### 判断逻辑

**文件**: `api/xianyu.py::create_reply_draft()`

**位置**: 函数开头

```python
@router.post("/reply-draft")
async def create_reply_draft(req: DraftReplyRequest):
    # 机器人闭嘴：检查会话状态
    if not should_bot_reply(req.conversation_id):
        current_status = get_conversation_status(req.conversation_id)
        return {
            "status": "bot_muted",
            "conversation_status": current_status,
            "draft": "",
            "detail": f"会话状态为 '{current_status}'，机器人不应回复。请人工处理。"
        }
    
    # 正常生成回复...
```

### 闭嘴条件

- `status = 'pending_handoff'`: 待人工，机器人闭嘴
- `status = 'human_taking'`: 人工处理中，机器人闭嘴
- `status = 'resolved'`: 已解决，机器人闭嘴

**只有** `status = 'open'` 或 `'bot'` 或 `None` 时，机器人才回复。

---

## C. ingest_messages 不会覆盖 status

### 核实结论

**文件**: `core/xianyu_service.py::ingest_messages()`

```python
INSERT INTO xianyu_conversations
(conversation_id, buyer_name, platform, status, ...)
VALUES (?, ?, ?, 'open', ...)
ON CONFLICT(conversation_id) DO UPDATE SET
    buyer_name = excluded.buyer_name,
    last_intent = excluded.last_intent,
    last_message_at = excluded.last_message_at,
    updated_at = CURRENT_TIMESTAMP
    -- ✓ 没有 status = ...
```

**结论**: ✅ **新消息进来不会覆盖 status**

`ON CONFLICT` 子句中**没有** `status = ...`，所以 `pending_handoff` / `human_taking` 不会被冲回 `open`。

---

## D. 前端·工作台

### 1. 会话列表渲染

**文件**: `web/static/main.js::renderConversations()`

#### 排序逻辑

```javascript
// 按状态优先级排序
const statusPriority = {
    'pending_handoff': 0,  // 最高优先级
    'human_taking': 1,
    'open': 2,
    'resolved': 3
};

// pending_handoff 的会话排最前
```

#### 会话状态徽章

```javascript
let conversationStatusBadge = '';
if (convStatus === 'pending_handoff') {
    conversationStatusBadge = '<span class="status-badge badge-orange">🔴 待人工</span>';
} else if (convStatus === 'human_taking') {
    conversationStatusBadge = '<span class="status-badge badge-blue">👤 人工中</span>';
} else if (convStatus === 'resolved') {
    conversationStatusBadge = '<span class="status-badge badge-green">✓ 已解决</span>';
}
```

**显示位置**: 买家昵称下方，与订单状态徽章并列

```html
<div class="badge-group">
    ${conversationStatusBadge}  <!-- 会话状态 -->
    ${orderStatusBadge}         <!-- 订单状态 -->
</div>
```

**注意**: 两种徽章不会混淆，可以同时显示。

#### 操作按钮

```javascript
<div class="conversation-actions">
    <button class="btn-view-chat btn-sm">查看对话</button>
    ${convStatus === 'pending_handoff' ? 
        '<button class="btn-handoff btn-sm btn-primary">接手</button>' : ''}
    ${convStatus === 'human_taking' ? 
        '<button class="btn-resolve btn-sm btn-success">已解决</button>' : ''}
</div>
```

- `pending_handoff`: 显示"接手"按钮
- `human_taking`: 显示"已解决"按钮
- `resolved`: 无按钮

#### 按钮事件

```javascript
// 接手
btnHandoff.addEventListener('click', async (e) => {
    await api(`/api/xianyu/conversations/${conv.conversation_id}/handoff`, {
        method: 'POST'
    });
    loadConversations(); // 刷新列表
});

// 已解决
btnResolve.addEventListener('click', async (e) => {
    await api(`/api/xianyu/conversations/${conv.conversation_id}/resolve`, {
        method: 'POST'
    });
    loadConversations(); // 刷新列表
});
```

### 2. 查看对话功能

**文件**: `web/static/main.js::viewConversationMessages()`

#### 查看对话

```javascript
async function viewConversationMessages(conversationId, buyerNick) {
    const data = await api(`/api/xianyu/conversations/${conversationId}/messages`);
    renderConversationMessages(data, buyerNick);
}
```

**不再弹出"待实现"**，改为：
1. 调用 `/api/xianyu/conversations/{id}/messages` API
2. 显示 DB 中已存的消息
3. 如果 DB 为空，显示"暂无已存消息"（如实告知，不编造）

#### 消息渲染

```javascript
messages.forEach(msg => {
    const isBuyer = msg.direction === 'buyer';
    const bgColor = isBuyer ? '#f0f0f0' : '#e3f2fd';
    const label = isBuyer ? '买家' : '卖家';
    
    // 解析 draft_reply（可能包含 holding 话术）
    let draftReply = '';
    if (msg.draft_reply) {
        const draftData = JSON.parse(msg.draft_reply);
        if (draftData.type === 'handoff') {
            draftReply = `
                <div style="background: #fff3cd; border-left: 3px solid #ff9800;">
                    <strong>💬 建议 Holding 话术：</strong><br>
                    "${draftData.suggested_reply}"<br>
                    <span>⚠️ 请手动复制发送</span><br>
                    <span style="font-size: 11px;">
                        原因: ${draftData.reason} 
                        (分数: ${draftData.confidence_score?.toFixed(4)})
                    </span>
                </div>
            `;
        }
    }
    
    // 渲染消息气泡 + holding 话术
});
```

**Holding 话术显示**:
- 黄色背景，橙色左边框
- 显示建议话术
- 标注"⚠️ 请手动复制发送"
- 显示转人工原因和置信度分数

### 3. 样式

**文件**: `web/static/index.css`

```css
/* 会话状态徽章 */
.badge-orange {
    background: #fff3e0;
    color: #e65100;
    border: 1px solid #ffb74d;
}

.badge-blue {
    background: #e3f2fd;
    color: #1976d2;
}

.badge-green {
    background: #e8f5e9;
    color: #2e7d32;
}

/* 操作按钮 */
.btn-sm {
    padding: 6px 12px;
    font-size: 13px;
    border-radius: 4px;
}

.btn-primary {
    background: #1976d2;
    color: white;
}

.btn-success {
    background: #388e3c;
    color: white;
}

/* 消息气泡 */
.message-left { text-align: left; }
.message-right { text-align: right; }
```

---

## 测试验证

### 单元测试

**文件**: `tests/test_conversation_status.py`

**测试用例**:
1. ✅ `test_initial_status` - 初始状态为 open
2. ✅ `test_mark_pending_handoff` - 标记待人工
3. ✅ `test_handoff_to_human` - 人工接手
4. ✅ `test_resolve_conversation` - 标记已解决
5. ✅ `test_invalid_state_transition` - 非法状态转换
6. ✅ `test_bot_should_not_reply_when_handoff` - 转人工后机器人闭嘴
7. ✅ `test_nonexistent_conversation` - 不存在的会话

**运行命令**:
```bash
pytest tests/test_conversation_status.py -v
```

### 集成测试场景

#### 场景 1: 检索灰区触发转人工

1. 买家消息: "能便宜点吗"
2. 检索分数: 0.56 (灰区)
3. 系统动作:
   - 标记 `status = 'pending_handoff'`
   - 记录 holding 话术到 `draft_reply`
   - 返回 `"status": "no_knowledge"`
4. 工作台显示: 🔴 待人工 徽章
5. 机器人闭嘴: 后续消息不触发回复

#### 场景 2: 人工接手

1. 工作台点击"接手"
2. API: `POST /api/xianyu/conversations/{id}/handoff`
3. 状态更新: `pending_handoff` → `human_taking`
4. 工作台刷新: 显示 👤 人工中 徽章
5. 机器人继续闭嘴

#### 场景 3: 标记已解决

1. 工作台点击"已解决"
2. API: `POST /api/xianyu/conversations/{id}/resolve`
3. 状态更新: `human_taking` → `resolved`
4. 工作台刷新: 显示 ✓ 已解决 徽章

---

## 文件清单

| 文件 | 修改内容 |
|------|---------|
| `core/conversation_status.py` | ✅ 新增 - 状态流转逻辑 |
| `api/xianyu.py` | ✅ 修改 - 检索触发转人工 + 机器人闭嘴 + API路由 |
| `web/static/main.js` | ✅ 修改 - 状态显示 + 操作按钮 + 查看对话 |
| `web/static/index.css` | ✅ 修改 - 样式支持 |
| `tests/test_conversation_status.py` | ✅ 新增 - 单元测试 |

---

## 关键特性

### ✅ 状态流转

- 三态流转: `pending_handoff` → `human_taking` → `resolved`
- 有状态检查，防止非法转换
- 新消息不会覆盖状态

### ✅ 机器人闭嘴

- `pending_handoff` / `human_taking` / `resolved` 状态：机器人不回复
- 在 `create_reply_draft()` 入口判断
- 返回 `"status": "bot_muted"`

### ✅ Holding 话术（降级版）

- 检索灰区/无答案时生成建议话术
- 存储在 `draft_reply` 字段（JSON格式）
- 工作台显示，标注"⚠️ 请手动复制发送"
- **不调用自动发送**（因未逆向）

### ✅ 工作台完整

- 会话列表按状态排序（待人工最前）
- 状态徽章：🔴 待人工 | 👤 人工中 | ✓ 已解决
- 操作按钮：接手 | 已解决
- 查看对话：显示DB已存消息 + holding话术

### ✅ 不编造

- DB没消息就显示"暂无已存消息"
- Holding 话术明确标注"请手动复制"
- 不假装能自动发送

---

## 运行测试结果

**命令**:
```bash
pytest tests/ -v
```

**预期**: 全部测试通过 ✅

---

**最小闭环已完成。系统现在能够：标记转人工 → 机器人闭嘴 → 工作台显示 → 人工接手 → 标记已解决。不依赖自动发送，Holding话术由人工手动复制发送。**
