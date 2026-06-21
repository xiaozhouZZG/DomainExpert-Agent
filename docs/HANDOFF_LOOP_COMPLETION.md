# 转人工回路补完 - 实施报告

**实施时间**: 2026-06-22 00:00
**状态**: ✅ 已完成

---

## 问题

原状态机 `resolved` 后机器人永久闭嘴 → 买家再来新问题，会话永久哑掉。

闲鱼会话是长期的，同一买家会反复来问不同问题。

---

## 解决方案

补充两条回到 bot 的路径：

### 1. 自动唤醒（resolved 收到新消息）

**触发条件**: `resolved` 会话收到新买家消息

**动作**: 自动转回 `open` 状态

**不唤醒**: `pending_handoff` / `human_taking` 不被新消息唤醒（人正在处理，别让机器人插进来）

### 2. 手动交回（工作台按钮）

**触发条件**: 人工点击"交回机器人"按钮

**适用状态**: `human_taking` / `resolved`

**动作**: 手动转回 `open` 状态

---

## 实施细节

### A. 自动唤醒逻辑

**文件**: `core/xianyu_service.py::ingest_buyer_messages()`

**位置**: 消息插入后

```python
# 插入买家消息
cursor.execute("""
    INSERT OR IGNORE INTO xianyu_messages
    (message_id, conversation_id, direction, content, ...)
    VALUES (?, ?, ?, ?, ...)
""", (...))

# 自动唤醒逻辑：resolved 会话收到新买家消息，转回 open
# 注意：只唤醒 resolved，pending_handoff/human_taking 不被唤醒
cursor.execute("""
    UPDATE xianyu_conversations
    SET status = 'open', updated_at = CURRENT_TIMESTAMP
    WHERE conversation_id = ?
      AND status = 'resolved'
""", (conversation_id,))

if cursor.rowcount > 0:
    logger.info(
        "Auto-wakeup: conversation %s status changed from resolved to open",
        conversation_id
    )
```

**命中条件**:
- ✅ `status = 'resolved'`: 自动唤醒
- ❌ `status = 'pending_handoff'`: 不唤醒（待人工，别插进来）
- ❌ `status = 'human_taking'`: 不唤醒（人正在处理）
- ❌ `status = 'open'`: 不需要唤醒（已经是 open）

**沿用现有 ON CONFLICT 不动 status**: 
- `ingest_buyer_messages` 的 `ON CONFLICT` 子句仍然不包含 `status = ...`
- 唤醒逻辑单独写在后面，只命中 `resolved`

---

### B. 手动交回机器人

#### 后端函数

**文件**: `core/conversation_status.py`

```python
def return_to_bot(conversation_id: str) -> dict[str, Any]:
    """
    手动交回机器人（human_taking / resolved → open）
    
    只允许从 human_taking 或 resolved 交回
    """
    # 检查当前状态
    current_status = get_conversation_status(conversation_id)
    
    if current_status not in ('human_taking', 'resolved'):
        raise ValueError(
            f"Cannot return conversation with status '{current_status}' to bot. "
            f"Expected 'human_taking' or 'resolved'"
        )
    
    # 更新状态为 open
    UPDATE xianyu_conversations
    SET status = 'open', updated_at = CURRENT_TIMESTAMP
    WHERE conversation_id = ?
    
    return {
        "status": "ok",
        "conversation_status": "open",
        "previous_status": current_status
    }
```

**允许交回的状态**:
- ✅ `human_taking`: 人工处理中，可以交回
- ✅ `resolved`: 已解决，可以交回
- ❌ `pending_handoff`: 还在待人工，不允许交回（必须先接手）
- ❌ `open`: 已经是机器人状态，不需要交回

#### API 路由

**文件**: `api/xianyu.py`

```python
@router.post("/conversations/{conversation_id}/return-to-bot")
async def return_conversation_to_bot(conversation_id: str):
    """手动交回机器人（human_taking / resolved → open）"""
    from core.conversation_status import return_to_bot
    
    try:
        result = return_to_bot(conversation_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

---

### C. 前端工作台

**文件**: `web/static/main.js`

#### 按钮显示

```javascript
<div class="conversation-actions">
    ${!conv.is_system ? '<button class="btn-view-chat btn-sm">查看对话</button>' : ''}
    ${convStatus === 'pending_handoff' ? '<button class="btn-handoff btn-sm btn-primary">接手</button>' : ''}
    ${convStatus === 'human_taking' ? '<button class="btn-resolve btn-sm btn-success">已解决</button>' : ''}
    ${(convStatus === 'human_taking' || convStatus === 'resolved') ? 
        '<button class="btn-return-bot btn-sm btn-secondary">交回机器人</button>' : ''}
</div>
```

**按钮规则**:
- `pending_handoff`: 显示"接手"
- `human_taking`: 显示"已解决" + "交回机器人"
- `resolved`: 显示"交回机器人"

#### 按钮事件

```javascript
const btnReturnBot = item.querySelector('.btn-return-bot');
if (btnReturnBot) {
    btnReturnBot.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`确认将会话 ${buyerNick} 交回机器人？`)) return;
        
        await api(`/api/xianyu/conversations/${conv.conversation_id}/return-to-bot`, {
            method: 'POST'
        });
        alert('交回成功，机器人将处理后续消息');
        loadConversations(); // 刷新列表
    });
}
```

#### 样式

**文件**: `web/static/index.css`

```css
.btn-secondary {
    background: #757575;
    color: white;
}

.btn-secondary:hover {
    background: #616161;
}
```

---

## 测试验证

### 单元测试

**文件**: `tests/test_conversation_status.py`

**新增测试用例**:

1. ✅ `test_auto_wakeup_resolved_on_new_message`
   - resolved 收到新消息 → 自动转回 open
   - 机器人可以回复

2. ✅ `test_no_wakeup_for_pending_handoff`
   - pending_handoff 收到新消息 → 仍然是 pending_handoff
   - 机器人继续闭嘴

3. ✅ `test_no_wakeup_for_human_taking`
   - human_taking 收到新消息 → 仍然是 human_taking
   - 机器人继续闭嘴

4. ✅ `test_return_to_bot_from_human_taking`
   - 从 human_taking 手动交回 → 转为 open
   - 机器人可以回复

5. ✅ `test_return_to_bot_from_resolved`
   - 从 resolved 手动交回 → 转为 open
   - 机器人可以回复

6. ✅ `test_cannot_return_to_bot_from_pending_handoff`
   - 从 pending_handoff 交回 → 抛出 ValueError
   - 必须先接手

### 测试结果

```
======================== 13 passed, 1 warning in 0.58s ========================

✅ test_initial_status                                  PASSED
✅ test_mark_pending_handoff                            PASSED
✅ test_handoff_to_human                                PASSED
✅ test_resolve_conversation                            PASSED
✅ test_invalid_state_transition                        PASSED
✅ test_bot_should_not_reply_when_handoff               PASSED
✅ test_nonexistent_conversation                        PASSED
✅ test_auto_wakeup_resolved_on_new_message             PASSED  [新增]
✅ test_no_wakeup_for_pending_handoff                   PASSED  [新增]
✅ test_no_wakeup_for_human_taking                      PASSED  [新增]
✅ test_return_to_bot_from_human_taking                 PASSED  [新增]
✅ test_return_to_bot_from_resolved                     PASSED  [新增]
✅ test_cannot_return_to_bot_from_pending_handoff       PASSED  [新增]
```

---

## 状态流转完整图

```
         [买家来消息]
              ↓
          ┌─ open ─┐
          │   ↑    │ [检索灰区/无答案]
          │   │    ↓
          │   │  pending_handoff
          │   │    ↓ [人工接手]
[手动交回]│   │  human_taking
          │   │    ↓ [已解决]
          │   └─ resolved
          │        ↑
          └────────┘
        [新消息自动唤醒]
```

**关键规则**:
1. `resolved` + 新消息 → 自动唤醒到 `open`
2. `pending_handoff` / `human_taking` + 新消息 → **不唤醒**
3. `human_taking` / `resolved` + 手动交回 → 转回 `open`
4. `pending_handoff` **不允许**手动交回（必须先接手）

---

## 文件清单

| 文件 | 修改内容 |
|------|---------|
| `core/xianyu_service.py` | ✅ 修改 - 添加自动唤醒逻辑 |
| `core/conversation_status.py` | ✅ 修改 - 添加 `return_to_bot()` 函数 |
| `api/xianyu.py` | ✅ 修改 - 添加 `/return-to-bot` API |
| `web/static/main.js` | ✅ 修改 - 添加"交回机器人"按钮 |
| `web/static/index.css` | ✅ 修改 - 添加按钮样式 |
| `tests/test_conversation_status.py` | ✅ 修改 - 添加 6 个测试用例 |

---

## 核心特性

### ✅ 自动唤醒逻辑正确

- `resolved` 收到新消息 → 自动转回 `open`
- `pending_handoff` / `human_taking` 收到新消息 → **不唤醒**
- 唤醒逻辑单独写，不影响 ON CONFLICT

### ✅ 手动交回逻辑正确

- `human_taking` / `resolved` 可以手动交回
- `pending_handoff` 不允许交回（防止绕过接手流程）
- API 有状态检查，防止非法转换

### ✅ 状态流转真实库读写

- 所有状态转换都有数据库操作
- 有日志记录（auto-wakeup）
- 单元测试覆盖完整

### ✅ 不会"进得去出不来"

- `resolved` 会话可以被新消息唤醒
- 人工可以手动交回机器人
- 会话可以反复进入人工/机器人状态

---

## 使用场景

### 场景 1: 买家反复咨询

1. 买家问题 A → 转人工 → 人工回复 → 标记 `resolved`
2. 买家问题 B（新问题）→ 自动唤醒为 `open`
3. 机器人处理问题 B

**结果**: 会话不会永久哑掉

### 场景 2: 人工主动交回

1. 买家问题 → 转人工 → 人工接手 (`human_taking`)
2. 人工回复完毕，认为后续问题机器人可以处理
3. 点击"交回机器人" → 转为 `open`
4. 买家后续问题由机器人处理

**结果**: 人工可以灵活控制

### 场景 3: pending_handoff 不被干扰

1. 买家问题 → 检索灰区 → 标记 `pending_handoff`
2. 买家又发了一条消息
3. 状态仍然是 `pending_handoff`（不被唤醒）
4. 人工看到两条消息，一起处理

**结果**: 待人工状态不会被新消息冲掉

---

## 总结

### 补充的两条回路

1. **自动唤醒**: `resolved` + 新消息 → `open`
2. **手动交回**: `human_taking` / `resolved` + 按钮 → `open`

### 保护机制

- `pending_handoff` / `human_taking` 不被新消息唤醒
- `pending_handoff` 不允许直接交回（防止绕过接手）

### 测试覆盖

- 13 个测试用例全部通过
- 覆盖自动唤醒、手动交回、状态保护

---

**回路补完完成。会话现在可以在人工和机器人之间反复流转，不会"进得去出不来"。**
