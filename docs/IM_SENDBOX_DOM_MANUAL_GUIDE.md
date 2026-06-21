# 闲鱼 IM 发送框 DOM 侦察报告

**任务**: Dump 闲鱼 IM 发送框真实 DOM，为自动发送做准备
**状态**: ⚠️ 需要手动操作

---

## 问题

浏览器 profile 被多个进程占用，无法通过脚本自动执行。

错误信息：
```
Browser profile is already in use by live process(es) 
[127120, 127552, 61836, 126896, 126012, 126620, 125408, 128528, 128540, 129036, 129152]
```

---

## 手动操作指南

由于浏览器已经在运行，需要手动执行以下步骤：

### 步骤 1: 打开闲鱼 IM 页面

1. 在已打开的浏览器中，导航到：
   ```
   https://goofish.com/im
   ```

2. 点击进入**一个测试会话**（不要用真实客户会话）

### 步骤 2: 打开浏览器开发者工具

- Windows: 按 `F12` 或 `Ctrl+Shift+I`
- Mac: 按 `Cmd+Option+I`

### 步骤 3: 在 Console 中执行以下代码

#### 3.1 查找输入框

```javascript
// 尝试多种选择器
const inputSelectors = [
    'textarea',
    'input[type="text"]',
    'div[contenteditable="true"]',
    'div[role="textbox"]',
    '[placeholder*="输入"]',
    '[placeholder*="消息"]',
    '[placeholder*="说点什么"]'
];

let inputElement = null;
let inputSelector = null;

for (const selector of inputSelectors) {
    const elem = document.querySelector(selector);
    if (elem) {
        inputElement = elem;
        inputSelector = selector;
        console.log('✓ 找到输入框:', selector);
        console.log('outerHTML:');
        console.log(elem.outerHTML);
        console.log('\n属性:');
        console.log('- tagName:', elem.tagName);
        console.log('- contentEditable:', elem.contentEditable);
        console.log('- placeholder:', elem.placeholder);
        console.log('- role:', elem.getAttribute('role'));
        console.log('- aria-label:', elem.getAttribute('aria-label'));
        break;
    }
}

if (!inputElement) {
    console.error('✗ 未找到输入框');
}
```

**请记录**:
- ✅ 找到的选择器
- ✅ 完整的 `outerHTML`
- ✅ 是 `<textarea>` / `<input>` / 还是 `contenteditable` 的 `<div>`

#### 3.2 查找发送按钮

```javascript
// 尝试多种选择器
const buttonSelectors = [
    'button:has-text("发送")',
    'button',
    '[aria-label*="发送"]'
];

let sendButton = null;
let buttonSelector = null;

// 手动查找包含"发送"文字的按钮
const allButtons = document.querySelectorAll('button');
for (const btn of allButtons) {
    if (btn.textContent.includes('发送')) {
        sendButton = btn;
        buttonSelector = 'button:has-text("发送")';
        console.log('✓ 找到发送按钮');
        console.log('outerHTML:');
        console.log(btn.outerHTML);
        console.log('\n属性:');
        console.log('- textContent:', btn.textContent);
        console.log('- type:', btn.type);
        console.log('- aria-label:', btn.getAttribute('aria-label'));
        break;
    }
}

if (!sendButton) {
    console.warn('⚠️  未找到包含"发送"的按钮');
    console.log('所有按钮:');
    allButtons.forEach((btn, i) => {
        console.log(`  按钮 ${i}:`, btn.textContent.trim(), btn.outerHTML.substring(0, 100));
    });
}
```

**请记录**:
- ✅ 找到的选择器
- ✅ 完整的 `outerHTML`
- ✅ 按钮文字内容
- ✅ 是否有 `aria-label` 或其他稳定属性

#### 3.3 测试回车发送

```javascript
// 测试输入框是否支持回车发送
if (inputElement) {
    console.log('\n测试回车发送:');
    console.log('请在输入框中按回车键，观察是否发送消息');
    console.log('⚠️  不要输入任何内容，只按回车');
    
    inputElement.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            console.log('检测到回车键');
            console.log('- shiftKey:', e.shiftKey);
            console.log('- ctrlKey:', e.ctrlKey);
            console.log('- metaKey:', e.metaKey);
            console.log('- defaultPrevented:', e.defaultPrevented);
        }
    });
}
```

**请测试并记录**:
- ✅ 回车是否发送消息
- ✅ 是否需要 Shift+Enter / Ctrl+Enter
- ✅ 单按回车的行为（发送 / 换行）

### 步骤 4: 截图

请截取以下内容：

1. **输入区全景**: 包含输入框和发送按钮的完整区域
2. **输入框特写**: 右键输入框 → 检查 → 截取高亮的元素
3. **发送按钮特写**: 右键发送按钮 → 检查 → 截取高亮的元素

---

## 需要贴回的信息

### 1. 输入框

```
选择器: ____________
类型: <textarea> / <input> / <div contenteditable="true">
placeholder: ____________
outerHTML:
<贴完整的 outerHTML>
```

### 2. 发送按钮

```
选择器: ____________
按钮文字: ____________
outerHTML:
<贴完整的 outerHTML>
```

### 3. 回车发送

```
单按回车: 发送 / 换行
Shift+Enter: ____________
Ctrl+Enter: ____________
```

### 4. 稳定定位建议

基于以上信息，建议使用的选择器：

**输入框**:
- 优先: `____________` (基于 placeholder / contenteditable / role)
- 备选: `____________`

**发送按钮**:
- 优先: `____________` (基于文字"发送" / aria-label)
- 备选: `____________`

---

## 铁律提醒

⚠️ **这一步只读 DOM，一条消息都不许发**

- 不要在输入框中输入任何内容
- 不要点击发送按钮
- 只截图、只读取 HTML 属性

---

## 下一步

拿到真实 DOM 结构后，才能在 `send_reply()` 中实现：

1. 定位输入框
2. 输入消息内容
3. 点击发送按钮 / 模拟回车
4. 等待发送完成

---

**请按照以上步骤操作，并将结果贴回。**
