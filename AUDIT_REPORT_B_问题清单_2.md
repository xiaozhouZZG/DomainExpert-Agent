# B. 问题清单（续）

## 🟡 中等问题4：GoofishPlaywrightPlatform每次创建新实例，headless auto-detect重复执行

### **问题描述**
每次调用 `_get_platform()` 都创建新的 `GoofishPlaywrightPlatform` 实例，但它们都获取同一个全局 `browser_manager`，导致headless设置混乱。

### **问题位置**
`tools/xianyu.py:56-63` + `platforms/goofish_playwright.py:35-65`

### **问题代码**
```python
# tools/xianyu.py:56-63
def _get_platform():
    from platforms.goofish_playwright import GoofishPlaywrightPlatform
    return GoofishPlaywrightPlatform()  # ❌ 每次创建新实例

# platforms/goofish_playwright.py:35-65
class GoofishPlaywrightPlatform:
    def __init__(self, headless: bool = None):
        self.storage_state_path = Path("data/browser_state/goofish.json")
        
        # ❌ 每次实例化都重新auto-detect
        if headless is None:
            self.headless = self.storage_state_path.exists()  # ← 文件存在=True
            logger.info("Auto-detected headless mode: %s", self.headless)
        
        # ✅ 但获取的是全局单例browser_manager
        self.browser_manager = get_goofish_browser_manager(
            headless=self.headless,  # ← 每次传不同的headless值？
        )
```

### **问题**
1. `GoofishPlaywrightPlatform()` 每次创建新实例
2. 每个实例都执行 `self.headless = self.storage_state_path.exists()`
3. 每个实例都调用 `get_goofish_browser_manager(headless=self.headless)`
4. 但 `get_goofish_browser_manager()` 返回全局单例，只有第一次调用时的 `headless` 值生效
5. 后续调用传入的 `headless` 值被忽略

### **证据**
```python
# platforms/browser_manager.py:560-577
_goofish_browser_manager = None

def get_goofish_browser_manager(*, headless: bool = False):
    global _goofish_browser_manager
    
    with _singleton_lock:
        if _goofish_browser_manager is None:
            _goofish_browser_manager = BrowserManager(
                headless=headless,  # ← 只有首次创建时用这个值
            )
        return _goofish_browser_manager  # ← 后续调用直接返回，忽略headless参数
```

### **后果**
- 第一次调用决定了 `headless` 值，后续无法改变
- `login_interactive` 想改成 `headless=False`，但可能无效
- 日志中会出现多次 "Auto-detected headless mode"，但实际只有第一次生效

---

## 🟡 中等问题5：错误处理缺口 - 静默失败和假数据

### **位置1：fetch_xianyu_competitors捕获所有异常**
**文件**: `tools/xianyu.py:195-206`

```python
except Exception as exc:
    logger.exception("fetch_xianyu_competitors failed")
    not_ready_detail = _playwright_not_ready_detail(exc)
    return json.dumps({
        "status": "not_ready" if not_ready_detail else "error",
        "detail": not_ready_detail or str(exc),
        "results": [],  # ← 返回空列表
        "persist": {"inserted": 0},
    })
```

**问题**: 任何错误都返回200成功响应，前端无法区分"真的没数据"和"抓取失败"。

**后果**: 
- 前端 `main.js:192` 检查 `if (!data.items || data.items.length === 0)` 
- 无法区分"未登录"、"抓取失败"、"真的没商品"

---

### **位置2：_invoke_tool_json只记录日志**
**文件**: `api/xianyu.py:1489-1497`

```python
@router.get("/competitors")
async def get_xianyu_competitors(keyword: str, limit: int = 20):
    try:
        result_json = await _invoke_tool_json(
            fetch_xianyu_competitors,
            {"keyword": keyword, "limit": limit}
        )
        return result_json  # ← 直接返回工具函数的JSON
    except Exception as exc:
        logger.exception("xianyu competitors API failed")
        raise HTTPException(status_code=502, detail=str(exc))
```

**问题**: 
- `fetch_xianyu_competitors` 捕获了所有异常并返回JSON
- 所以 `except Exception` 分支永远不会执行
- 错误被包装在JSON的 `status` 字段里，但HTTP状态码还是200

---

### **位置3：greeting接口兜底假数据**
**文件**: `api/greeting.py:11, 54-56, 64-66`

```python
FALLBACK_GREETING = "您好，我是智能客服，很高兴为您服务~ 请问有什么可以帮您？"

try:
    greeting = await client.chat(messages, temperature=0.8)
    
    if not greeting or not greeting.strip():
        logger.warning("[问候接口] LLM 返回空，使用固定问候")
        return {"greeting": FALLBACK_GREETING}  # ← 兜底假数据
    
    return {"greeting": greeting}

except Exception as e:
    logger.error(f"[问候接口] 生成失败: {e}")
    return {"greeting": FALLBACK_GREETING}  # ← 兜底假数据
```

**问题**: LLM失败时返回固定假数据，前端无法知道是真实生成还是兜底。

---

### **位置4：loadListings静默失败**
**文件**: `web/static/main.js:93-109`

```javascript
async function loadListings() {
    const container = document.getElementById('listings-container');
    container.innerHTML = '<div class="loading">加载中...</div>';

    try {
        const data = await api('/api/xianyu/listings?limit=50');

        if (!data.listings || data.listings.length === 0) {
            container.innerHTML = '<div class="placeholder-card">暂无商品</div>';
            return;  // ← 空数据和错误都显示"暂无商品"
        }

        // 渲染商品列表...
    } catch (error) {
        console.error('加载商品失败:', error);
        container.innerHTML = `<div class="error">加载失败</div>`;  // ← 只在JS异常时显示
    }
}
```

**问题**: 
- `data.listings` 为空可能是"真的没商品"或"未登录"或"抓取失败"
- 无法区分，统一显示"暂无商品"

---

## 🟡 中等问题6：重复实现和写死的假数据

### **位置1：两套登录检测**
**文件**: `platforms/goofish_playwright.py:946, 1061`

```python
# 函数1：_detect_login_state (行946-981)
def _detect_login_state(self, page):
    storage_summary = summarize_storage_state(self.storage_state_path)
    logged_out = self._looks_logged_out(page)  # ← 调用函数2
    has_complete_state = bool(...)
    
    if logged_out:
        return {"logged_in": False}
    if has_complete_state:
        return {"logged_in": True}
    return {"logged_in": False}

# 函数2：_looks_logged_out (行1061-1105)
def _looks_logged_out(self, page):
    # 检查URL
    if any(token in page.url.lower() for token in ("login.taobao.com", ...)):
        return True
    
    # 检查页面元素
    has_account_entry = page.evaluate(...)
    if has_account_entry:
        return False
    
    has_login_entry = page.evaluate(...)
    if has_login_entry:
        return True
    
    return False
```

**问题**: 
- `_detect_login_state` 同时检查文件和页面
- `_looks_logged_out` 只检查页面
- 两个函数部分重复，逻辑混乱

---

### **位置2：两套HTML页面（旧版和新版）**
**文件**: `app.py:103-110`

```python
@app.get("/xianyu-old")
async def xianyu_old_page():
    return FileResponse("web/xianyu.html")  # ← 旧版

@app.get("/dashboard-old")
async def dashboard_old_page():
    return FileResponse("web/dashboard.html")  # ← 旧版
```

**对应前端文件**:
- `web/xianyu.html` (17830 字节) + `web/static/xianyu.js` (46893 字节) + `web/static/xianyu.css` (18118 字节)
- `web/dashboard.html` (13665 字节) + `web/static/dashboard.js` (16151 字节) + `web/static/dashboard.css` (19374 字节)

**新版**:
- `web/main.html` (10154 字节) + `web/static/main.js` (15199 字节) + `web/static/main.css` (9275 字节)

**问题**: 
- 旧版路由标记为 `-old` 但还在
- 前端资源文件重复，维护困难
- 不清楚是否还有人在用旧版

---

### **位置3：写死的假Token**
**文件**: `api/admin.py:37-46`

```python
from api.middleware import verify_admin_token

def verify_admin_token(authorization: str = Header(None)):
    expected = "change-me-token"  # ❌ 写死的token
    
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    token = authorization.replace("Bearer ", "").strip()
    
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return token
```

**问题**: 
- Token写死在代码里
- 实际应该从环境变量或配置文件读取
- "change-me-token" 明显是临时占位符

---

### **位置4：seed.py中的假数据**
**文件**: `seed.py:1-8238` (整个文件)

```python
# seed.py:78-93
def seed_competitor_observations():
    """Insert fake competitor data for testing"""
    fake_competitors = [
        {
            "title": "iPhone 13 Pro 128GB",
            "price": 5999.00,
            "sold_count": 120,
            "platform": "goofish",
            "source_label": "seed_script",
            "raw_json": json.dumps({...}),
            "observed_at": "2024-01-15 10:30:00"
        },
        # ... 更多假数据
    ]
```

**问题**: 
- 大量硬编码假数据
- 不清楚是否还在使用
- 混淆真实数据和测试数据

---

## 🟢 小问题7：其他代码质量问题

### **问题7.1：日志编码问题**
**文件**: 多处日志输出

```
日志示例：
INFO:     127.0.0.1:63542 - "GET /api/xianyu/login/status HTTP/1.1" 200 OK
2026-06-21 11:46:22,586 - reusing Goofish browser context: 
    user_data_dir=E:\AIClaudeAI��������\AI��ģ��RAG�������忪��\data\browser_state\goofish_profile
```

**问题**: Windows控制台GBK编码，中文路径显示为乱码。

---

### **问题7.2：未使用的导入**
**文件**: `api/xianyu.py:1-33`

```python
import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field

from api.middleware import verify_admin_token
from core.xianyu_service import XianyuService  # ← 可能未使用
from database.connection import get_db_connection
from platforms.base import PlatformNotLoggedInError, PlatformNotReadyError
from platforms.browser_manager import summarize_storage_state
from tools.xianyu import (
    fetch_xianyu_competitors,
    read_xianyu_listings,
    read_xianyu_messages,
    # ... 等
)
```

**问题**: `XianyuService` 可能未使用，需要检查。

---

### **问题7.3：magic number**
**文件**: 多处

```python
# platforms/goofish_playwright.py:145, 149, 170, 1123
max_wait_seconds = 300  # ← 应该定义为常量
if time.time() - last_save_at >= 5:  # ← 魔法数字5
time.sleep(2)  # ← 魔法数字2
deadline = time.time() + 60  # ← 魔法数字60

# main.js:385
const checkInterval = setInterval(async () => { ... }, 2000);  # ← 魔法数字2000
```

**建议**: 定义为常量，如 `SAVE_INTERVAL_SECONDS = 5`, `POLL_INTERVAL_MS = 2000`

---
