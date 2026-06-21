# B. 问题清单（按严重度排序）

## 🔴 严重问题1：点"扫码登录"没反应 - JS错误未处理

### **问题描述**
用户点击"扫码登录"按钮可能没有任何反应，浏览器控制台可能有JS错误。

### **代码位置**
`web/main.html:183` + `web/static/main.js:375-402`

### **绑定代码**
```html
<!-- web/main.html:183 -->
<button class="btn-primary" onclick="startLogin()">扫码登录</button>
```

```javascript
// web/static/main.js:375-402
async function startLogin() {
    try {
        const data = await api('/api/xianyu/login/start', {
            method: 'POST',
            body: JSON.stringify({ max_wait_seconds: 300 })
        });

        alert('请在弹出的浏览器中扫码登录闲鱼');

        const checkInterval = setInterval(async () => {
            try {
                const status = await api('/api/xianyu/login/status');
                if (status.login_state === 'completed') {  // ❌ 错误属性名
                    clearInterval(checkInterval);
                    alert('登录成功！');
                    loadSettings();
                } else if (status.login_state === 'failed') {  // ❌ 错误属性名
                    clearInterval(checkInterval);
                    alert('登录失败，请重试');
                }
            } catch (error) {
                clearInterval(checkInterval);
            }
        }, 2000);
    } catch (error) {
        alert('启动登录失败: ' + error.message);
    }
}
```

### **问题1.1：轮询条件错误**
**位置**: `main.js:388, 392`

**问题**:
```javascript
if (status.login_state === 'completed') {  // ❌ 错误
```

**实际返回结构** (`api/xianyu.py:173-196`):
```json
{
  "running": false,
  "status": "success",  // ← 应该检查这个
  "message": "...",
  "login_state": {      // ← 这是对象，不是字符串
    "logged_in": true,
    "status": "logged_in"
  }
}
```

**正确判断**:
```javascript
if (!status.running && status.status === 'success') {  // ✅ 正确
```

**后果**: 轮询永远不会停止，用户扫码成功后也看不到"登录成功"提示。

---

### **问题1.2：api函数可能返回401导致死循环**
**位置**: `main.js:10-30`

**问题代码**:
```javascript
async function api(path, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    if (state.token) {
        headers['Authorization'] = `Bearer ${state.token}`;
    }

    const response = await fetch(path, {
        ...options,
        headers
    });

    if (response.status === 401) {
        promptLogin();  // ← 弹出token输入框
        throw new Error('需要管理员认证');
    }

    return response.json();
}
```

**问题**: 
- 如果用户没有设置token，第一次调用就会弹出token输入框
- 如果token错误，每2秒轮询一次，每次都弹窗提示输入token
- `promptLogin()` 会 `location.reload()`，导致页面刷新，登录任务丢失

**证据**: `main.js:33-41`
```javascript
function promptLogin() {
    const token = prompt('请输入管理员Token (DEFAULT_TOKEN):');
    if (token) {
        state.token = token.trim().replace(/^Bearer\s+/i, '');
        localStorage.setItem('admin_token', state.token);
        location.reload();  // ❌ 刷新页面，登录任务丢失
    }
}
```

---

## 🔴 严重问题2：设置页显示"已登录"，竞品却报"未登录" - 多套登录态判断打架

### **问题描述**
`/api/xianyu/status` 返回已登录，`/api/xianyu/competitors` 却报未登录。

### **判断位置汇总**

#### **判断1：前端显示状态**
**文件**: `web/static/main.js:348-357`

```javascript
const data = await api('/api/xianyu/status');

if (data.storage_state_exists) {  // ← 只检查文件是否存在
    statusEl.textContent = '已登录';
} else {
    statusEl.textContent = '未登录';
}
```

**问题**: 只要文件存在就显示"已登录"，不管文件是否有效。

---

#### **判断2：后端/status接口**
**文件**: `api/xianyu.py:123-137`

```python
@router.get("/status")
async def get_xianyu_status():
    storage_state = Path("data/browser_state/goofish.json")
    profile_dir = Path("data/browser_state/goofish_profile")
    login_task = _public_login_task()
    login_state = _login_state_for_task(login_task)  # ← 调用判断3
    return {
        "storage_state_exists": storage_state.exists(),  # ← 浅层判断
        "login_state": login_state,  # ← 深层判断
    }
```

**问题**: 返回了两套判断结果，但前端只用了浅层的 `storage_state_exists`。

---

#### **判断3：_stored_login_state**
**文件**: `api/xianyu.py:296-319`

```python
def _stored_login_state():
    storage_state = Path("data/browser_state/goofish.json")
    summary = summarize_storage_state(storage_state)
    stored_state_complete = bool(
        summary.get("valid")
        and summary.get("important_cookie_count", 0) >= 4
        and summary.get("local_storage_item_count", 0) >= 1
        and summary.get("size", 0) >= 20_000
    )
    return {
        "logged_in": stored_state_complete,  # ✅ 修复后：完整=True
        "status": "logged_in" if stored_state_complete else "not_logged_in",
    }
```

**问题**: 检查文件完整性，但前端没用这个判断。

---

#### **判断4：抓取前检查（文件是否存在）**
**文件**: `platforms/goofish_playwright.py:283-284`

```python
def fetch_competitor(self, keyword: str, limit: int = 20):
    if not self.storage_state_path.exists():  # ← 只检查文件存在
        raise PlatformNotLoggedInError("goofish login state not found")
```

**问题**: 只检查文件是否存在，不检查是否有效。

---

#### **判断5：抓取中检查（页面是否显示登录按钮）**
**文件**: `platforms/goofish_playwright.py:305-306, 1127-1128`

```python
# 位置1：fetch_competitor函数
if card_count == 0:
    if self._looks_logged_out(page):  # ← 检查页面
        raise PlatformNotLoggedInError("please scan QR code and login")

# 位置2：_wait_for_search_results函数
while time.time() < deadline:
    if self._looks_logged_out(page):  # ← 每轮循环检查
        raise PlatformNotLoggedInError("please scan QR code and login")
```

**核心判断**: `platforms/goofish_playwright.py:1061-1105`

```python
def _looks_logged_out(self, page):
    # 检查1：URL包含登录页标志
    if any(token in page.url.lower() for token in ("login.taobao.com", "passport", "login.htm")):
        return True
    
    # 检查2：页面有"个人中心"入口（已登录）
    has_account_entry = page.evaluate(
        r"() => Array.from(document.querySelectorAll('a[href*=\"/personal\"]')).some(...)"
    )
    if has_account_entry:
        return False  # ← 已登录
    
    # 检查3：页面有"登录"按钮（未登录）
    has_login_entry = page.evaluate(
        r"() => Array.from(document.querySelectorAll('a,button')).some((el) => el.text === '登录')"
    )
    if has_login_entry:
        return True  # ← 未登录
    
    return False
```

**问题**: 这是**真正的登录态检查**（检查浏览器实际页面），但之前的判断1-4都是**假判断**（只检查文件）。

---

### **根因总结**

| 判断位置 | 检查内容 | 使用场景 | 问题 |
|---------|---------|---------|------|
| 判断1 (前端) | 文件是否存在 | 设置页显示 | ❌ 浅层假判断 |
| 判断2 (status接口) | 文件是否存在 | API返回 | ❌ 返回两套结果 |
| 判断3 (_stored_login_state) | 文件完整性 | API返回 | ✅ 深层判断，但前端没用 |
| 判断4 (抓取前) | 文件是否存在 | 抓取启动前 | ❌ 浅层假判断 |
| 判断5 (抓取中) | 页面实际状态 | 浏览器抓取中 | ✅ 真实判断 |

**矛盾**:
- 前端用判断1（文件存在）→ 显示"已登录"
- 抓取用判断5（页面检查）→ 报"未登录"
- 判断1-4都是**离线判断**（不打开浏览器）
- 判断5是**在线判断**（实时检查页面）
- 文件可能存在但已失效，离线判断无法发现

---

## 🔴 严重问题3：browser_manager单例复用旧context

### **问题描述**
登录成功后，抓取时仍然用旧context，新的goofish.json没被加载。

### **问题位置**
`platforms/browser_manager.py:174-183, 560-577`

### **单例机制**
```python
# platforms/browser_manager.py:560-577
_goofish_browser_manager = None  # ← 全局变量

def get_goofish_browser_manager():
    global _goofish_browser_manager
    
    with _singleton_lock:
        if _goofish_browser_manager is None:
            _goofish_browser_manager = BrowserManager(...)  # ← 首次创建
        return _goofish_browser_manager  # ← 后续复用
```

### **复用逻辑**
```python
# platforms/browser_manager.py:174-183
def _ensure_started_locked(self, action_name):
    if self._context_is_live_locked():
        logger.info("reusing Goofish browser context: context_id=%s", id(self._context))
        return self._context  # ← 复用现有context，不重新加载goofish.json
    
    # 只有context为None时才重新启动
    self._context = self._playwright.chromium.launch_persistent_context(...)
    self._restore_storage_state_locked()  # ← 只在启动时加载一次
    return self._context
```

### **问题链路**
```
1. 服务启动
2. 用户访问 → 前端轮询/status → 触发browser_manager启动
   → 加载旧goofish.json（失效）→ context A保存在内存
3. 用户扫码登录 → 保存新goofish.json ✅
   → login_interactive调用stop() ✅
   → 但全局单例_goofish_browser_manager还指向这个BrowserManager对象 ❌
4. 用户搜竞品 → 获取browser_manager（从全局单例）
   → 检测context已关闭 → 重新启动
   → 但BrowserManager对象的self.headless等属性还是旧的 ❌
   → 可能加载了错误的配置
```

### **修复证据**
`platforms/goofish_playwright.py:185-189` (已修复)

```python
result = self.browser_manager.with_page("login_interactive", action)

# ✅ 登录成功后：清空全局单例
logger.info("login completed, shutting down and clearing singleton")
from platforms.browser_manager import shutdown_goofish_browser_manager
shutdown_goofish_browser_manager()  # ← 清空全局单例

return result
```

**shutdown函数**: `platforms/browser_manager.py:580-588`

```python
def shutdown_goofish_browser_manager():
    global _goofish_browser_manager
    
    with _singleton_lock:
        manager = _goofish_browser_manager
        _goofish_browser_manager = None  # ✅ 清空全局单例
    
    if manager is not None:
        manager.stop()  # ✅ 关闭浏览器
```

---
