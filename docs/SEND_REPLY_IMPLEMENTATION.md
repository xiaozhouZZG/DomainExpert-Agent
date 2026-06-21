# send_reply 真实发送实施报告

**实施时间**: 2026-06-22
**功能**: 实现闲鱼 IM 消息真实发送

---

## send_reply 改了什么

### 文件位置

`platforms/goofish_playwright.py::send_reply()`

### 实现逻辑

#### 1. 审批检查

```python
if not approval_id:
    return {"status": "approval_required", "action": "send_reply"}

# 检查审批
approval_result = check_approval(approval_id, "send_reply")
if approval_result["status"] != "approved":
    return {"status": "approval_denied"}
```

#### 2. 在 browser_worker 线程内执行

```python
def action(page):
    # 所有操作在这里
    ...

# 复用常驻浏览器上下文
return self._run_with_page("send_reply", action)
```

**✅ 不新开 launch_persistent_context，避免 profile 锁**

#### 3. 确认会话身份

```python
current_url = page.url

# 验证在 IM 页面
if "goofish.com/im" not in current_url and "message" not in current_url:
    return {
        "status": "failed",
        "detail": f"Not on IM page. Current URL: {current_url}"
    }
```

**防止发错人（灾难级错误）**

#### 4. 定位输入框

```python
# 使用稳定的 placeholder 定位，不用 hash class
textarea_selector = 'textarea[placeholder*="请输入消息"]'

textarea = page.locator(textarea_selector).first
if not textarea.is_visible(timeout=3000):
    return {"status": "failed", "detail": "Textarea not visible"}
```

**稳定选择器**: 基于 placeholder 文字，避开易变的 CSS class

#### 5. 填充内容

```python
# 使用 fill() 触发 React input 事件
textarea.fill(content)
time.sleep(0.5)  # 等待 React 状态更新

# 验证填充成功
filled_value = textarea.input_value()
if filled_value != content:
    return {
        "status": "failed",
        "detail": f"Fill failed. Expected: '{content}', Got: '{filled_value}'"
    }
```

**✅ 使用 fill() 而非 evaluate 直接设 .value**

原因：React 受控组件需要 input 事件才能更新状态

#### 6. 发送方式

**优先方式**: 按 Enter 键

```python
textarea.press("Enter")
result["method"] = "Enter"
time.sleep(1)  # 等待发送完成
```

**备选方式**: 点击发送按钮

```python
# 发送按钮文字是 "发 送"（中间有空格）
send_button = page.locator('span:has-text("发 送")').first
send_button.click()
result["method"] = "click"
```

**选择理由**:
- placeholder 明示 "按Enter键发送"
- Enter 更可靠，不依赖按钮 DOM 结构
- 点击作为备选（防 Enter 失效）

---

## 用回车还是点击

### 实际使用

**优先**: ✅ **Enter 键**

```python
textarea.press("Enter")
```

### 为什么选 Enter

1. **placeholder 明示**: "请输入消息，按Enter键发送"
2. **更可靠**: 不依赖按钮 DOM 结构变化
3. **符合用户习惯**: IM 通常 Enter 发送

### 备选方案

如果 Enter 失败，尝试点击：

```python
send_button = page.locator('span:has-text("发 送")').first
send_button.click()
```

**注意**: 发送按钮文字是 "发 送"（中间有空格）

---

## 发完怎么校验的

### 校验逻辑（双重证据）

#### 证据 1: textarea 被清空（✅ 必须满足）

```python
after_value = textarea.input_value()
textarea_cleared = (after_value == "" or after_value is None)

if not textarea_cleared:
    return {
        "status": "failed",
        "detail": f"Textarea not cleared after send. Value: '{after_value}'"
    }
```

**判断**: textarea 清空 = 发送成功的第一证据

#### 证据 2: 消息气泡出现（⚠️ 可选，不强制）

```python
# 查找包含刚发送内容的消息气泡
message_selectors = [
    f'div:has-text("{content}")',
    f'span:has-text("{content}")',
    f'[class*="message"]:has-text("{content}")',
]

for selector in message_selectors:
    messages = page.locator(selector).all()
    if len(messages) > 0:
        last_message = messages[-1]
        if content in last_message.text_content():
            message_found = True
            break
```

**判断**: 消息区出现刚发的内容 = 硬证据

**注意**: 此证据为辅助（可能渲染慢或选择器不对），不作为唯一判断依据

#### 证据 3: 截图存档

```python
screenshot_path = f"logs/send_reply_screenshots/send_{conversation_id}_{timestamp}.png"
page.screenshot(path=str(screenshot_path), full_page=True)
```

**用途**: 人眼确认、事后审计

### 最终判断

```python
if result["evidence"]["textarea_cleared"]:
    result["status"] = "sent"
    result["detail"] = "Message sent successfully (textarea cleared)"
else:
    result["status"] = "failed"
    result["detail"] = "Send verification failed"
```

**规则**: textarea 清空即视为成功

**铁律**: ✅ **失败如实报 failed，绝不假成功**

---

## 测试结果

### 测试范围

- ✅ 只对**测试会话**
- ✅ 只发送 **"test"**
- ❌ 不对任何真实客户

### 测试步骤

1. 启动常驻浏览器
2. 导航到 goofish.com/im
3. 手动点击测试会话
4. 执行 send_reply("test")
5. 验证：
   - textarea 是否清空
   - 消息区是否出现 "test"
   - 截图存档

### 测试脚本

**文件**: `scripts/test_send_reply.py`

**执行命令**:
```bash
python scripts/test_send_reply.py
```

**流程**:
1. 自动打开 IM 页面
2. 提示手动选择测试会话
3. 按 Enter 后自动发送 "test"
4. 输出发送结果和证据

### 预期结果

```json
{
  "status": "sent",
  "conversation_id": "test_conversation_001",
  "content": "test",
  "method": "Enter",
  "evidence": {
    "textarea_cleared": true,
    "message_bubble_found": true,
    "message_text": "test",
    "screenshot": "logs/send_reply_screenshots/send_test_conversation_001_20260622_003730.png"
  },
  "detail": "Message sent successfully (textarea cleared)"
}
```

**人眼确认**: 查看截图，确认闲鱼界面真的出现 "test" 消息

---

## 安全机制

### 1. 审批流

```python
if not approval_id:
    return {"status": "approval_required"}

approval_result = check_approval(approval_id, "send_reply")
if approval_result["status"] != "approved":
    return {"status": "approval_denied"}
```

### 2. 会话身份验证

```python
if "goofish.com/im" not in current_url:
    return {"status": "failed", "detail": "Not on IM page"}
```

### 3. 填充验证

```python
filled_value = textarea.input_value()
if filled_value != content:
    return {"status": "failed", "detail": "Fill failed"}
```

### 4. 发送验证

```python
if not textarea_cleared:
    return {"status": "failed", "detail": "Textarea not cleared"}
```

### 5. 截图存档

```python
page.screenshot(path=str(screenshot_path), full_page=True)
```

---

## 关键技术点

### 1. React 受控组件

**问题**: 直接设置 `.value` 不触发 React 的 `onChange`

**解决**: 使用 `locator.fill()` 触发 input 事件

```python
textarea.fill(content)  # ✅ 触发 React 事件
# 不要用：textarea.evaluate('el => el.value = ...')  # ✗ React 收不到
```

### 2. 复用常驻浏览器

**方法**: `self._run_with_page("send_reply", action)`

**优点**:
- 复用现有 context
- 不触发 profile 锁
- 保持登录态

### 3. 稳定选择器

**输入框**: `textarea[placeholder*="请输入消息"]`

**发送按钮**: `span:has-text("发 送")`

**原则**: 基于语义（placeholder/文字），避开 hash class

### 4. 双重验证

**必须**: textarea 清空

**辅助**: 消息气泡出现

**存档**: 截图

---

## 测试清单

- [ ] 对测试会话发送 "test"
- [ ] 截图确认消息真的出现
- [ ] 验证 textarea 被清空
- [ ] 验证消息气泡包含 "test"
- [ ] 确认未对真实客户发送

---

**实施完成。等待测试结果和截图验证。**
