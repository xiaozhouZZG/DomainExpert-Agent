# A. 核心流程地图（续）

## 流程4：搜索竞品 → 实时抓取 → 返回结果

**前端**: `web/main.js:179-243`

```
1. 用户进入"竞品选品"模块
   → main.html:29-31
   <li class="nav-item" data-module="research">
   
   → main.html:113-153 竞品选品模块内容

2. 用户输入"鼠标"并点击"搜索"
   → main.html:128-130
   <input type="text" id="competitor-keyword" placeholder="搜索关键词，如: iPhone 12">
   <button class="btn-primary" onclick="searchCompetitors()">搜索</button>

3. searchCompetitors() 函数
   → main.js:179-243

   async function searchCompetitors() {
       const keyword = document.getElementById('competitor-keyword').value.trim();
       const resultArea = document.getElementById('competitors-result');
       resultArea.innerHTML = '<div class="loading">实时抓取中，请稍候...</div>';
       
       try {
           const data = await api(`/api/xianyu/competitors?keyword=${keyword}&limit=20`);
           
           // ✅ 根据真实状态显示不同提示
           if (data.status === 'not_logged_in') {
               resultArea.innerHTML = '未登录，请先扫码登录闲鱼';
               return;
           }
           
           if (data.status === 'error' || data.status === 'failed') {
               resultArea.innerHTML = '抓取失败：' + data.detail;
               return;
           }
           
           if (!data.items || data.items.length === 0) {
               resultArea.innerHTML = '未找到"鼠标"相关在售商品';
               return;
           }
           
           // 显示竞品列表
           const avgPrice = data.items.reduce((sum, item) => sum + parseFloat(item.price || 0), 0) / data.items.length;
           resultArea.innerHTML = `
               <h3>竞品分析结果</h3>
               <div>平均价格: ¥${avgPrice.toFixed(2)} | 样本数量: ${data.items.length}</div>
               ${data.items.map(item => `
                   <div class="listing-card">
                       <div class="listing-title">${item.title}</div>
                       <div class="listing-price">¥${item.price}</div>
                   </div>
               `).join('')}
           `;
       } catch (error) {
           resultArea.innerHTML = `<div class="error">${error.message}</div>`;
       }
   }
```

**后端API**: `api/xianyu.py:1484-1497`

```
4. GET /api/xianyu/competitors?keyword=鼠标&limit=20
   → api/xianyu.py:1484-1497

   @router.get("/competitors")
   async def get_xianyu_competitors(
       keyword: str,
       limit: int = 20,
       token: str = Depends(verify_admin_token)
   ):
       try:
           result_json = await _invoke_tool_json(
               fetch_xianyu_competitors,
               {"keyword": keyword, "limit": limit}
           )
           return result_json
       except Exception as exc:
           logger.exception("xianyu competitors API failed")
           raise HTTPException(status_code=502, detail=str(exc))

5. _invoke_tool_json(fetch_xianyu_competitors, args)
   → api/xianyu.py:2073-2087

   async def _invoke_tool_json(func, input_dict):
       loop = asyncio.get_running_loop()
       result_str = await loop.run_in_executor(None, func, **input_dict)
       return json.loads(result_str)  # ← 返回JSON对象
```

**工具函数**: `tools/xianyu.py:166-206`

```
6. fetch_xianyu_competitors(keyword="鼠标", limit=20)
   → tools/xianyu.py:166-206

   def fetch_xianyu_competitors(keyword: str, limit: int = 20):
       try:
           results = _get_platform().fetch_competitor(keyword=keyword, limit=limit)
           persist = persist_competitor_observations(results)
           return json.dumps({
               "status": "success",
               "results": results,
               "persist": persist
           })
       except PlatformNotLoggedInError as exc:
           return json.dumps({
               "status": "not_logged_in",  # ← 未登录错误
               "detail": str(exc),
               "results": []
           })
       except Exception as exc:
           return json.dumps({
               "status": "error",
               "detail": str(exc),
               "results": []
           })

7. _get_platform()
   → tools/xianyu.py:56-63

   def _get_platform():
       from platforms.goofish_playwright import GoofishPlaywrightPlatform
       return GoofishPlaywrightPlatform()  # ← 每次创建新实例
```

**平台实例化**: `platforms/goofish_playwright.py:35-65`

```
8. GoofishPlaywrightPlatform()
   → platforms/goofish_playwright.py:35-65

   def __init__(self):
       self.storage_state_path = Path("data/browser_state/goofish.json")
       self.user_data_dir = Path("data/browser_state/goofish_profile")
       
       # ❌ 问题：auto-detect headless模式
       if headless is None:
           self.headless = self.storage_state_path.exists()  # ← 文件存在=True
           logger.info("Auto-detected headless mode: %s", self.headless)
       
       # 获取全局单例browser_manager
       self.browser_manager = get_goofish_browser_manager(
           user_data_dir=str(self.user_data_dir),
           storage_state_path=str(self.storage_state_path),
           headless=self.headless,
           slow_mo_ms=500
       )

9. get_goofish_browser_manager()
   → platforms/browser_manager.py:560-577

   def get_goofish_browser_manager():
       global _goofish_browser_manager
       
       with _singleton_lock:
           if _goofish_browser_manager is None:
               _goofish_browser_manager = BrowserManager(
                   user_data_dir=user_data_dir,
                   storage_state_path=storage_state_path,
                   headless=headless,
                   slow_mo_ms=slow_mo_ms
               )
           return _goofish_browser_manager  # ← 返回全局单例
```

**抓取竞品**: `platforms/goofish_playwright.py:281-338`

```
10. platform.fetch_competitor(keyword="鼠标", limit=20)
    → platforms/goofish_playwright.py:281-338

    def fetch_competitor(self, keyword: str, limit: int = 20):
        # 检查登录态文件是否存在
        if not self.storage_state_path.exists():
            raise PlatformNotLoggedInError("goofish login state not found")
        
        def action(page):
            # 打开搜索页
            search_url = GOOFISH_URLS["search"].format(keyword=quote_plus(keyword))
            self._goto_domcontentloaded_or_body(page, search_url)
            
            # 等待搜索结果加载
            self._wait_for_search_results(page, keyword)
            
            # 获取商品卡片
            cards_locator = page.locator(GOOFISH_SELECTORS["search_result_card"])
            card_count = cards_locator.count()
            
            if card_count == 0:
                # ❌ 关键检查点：页面是否显示未登录
                if self._looks_logged_out(page):
                    raise PlatformNotLoggedInError("please scan QR code and login")
                return []
            
            # 提取商品信息
            results = []
            for idx in range(min(card_count, limit)):
                card = cards_locator.nth(idx)
                parsed = self._extract_competitor_card(card, keyword, idx)
                if parsed["title"] or parsed["price"]:
                    results.append(parsed)
            
            return results
        
        return self._run_with_page("fetch_competitor", action)

11. self._wait_for_search_results(page, keyword)
    → platforms/goofish_playwright.py:1120-1154

    def _wait_for_search_results(self, page, keyword):
        card_selector = GOOFISH_SELECTORS["search_result_card"]
        deadline = time.time() + 60
        
        while time.time() < deadline:
            # ❌ 关键检查点：每轮循环检查是否未登录
            if self._looks_logged_out(page):
                raise PlatformNotLoggedInError("please scan QR code and login")
            
            card_count = page.locator(card_selector).count()
            if card_count > 0:
                return {"count": card_count}
            
            text = page.locator("body").inner_text()
            if "暂无相关宝贝" in text or "没有找到相关" in text:
                return {"count": 0}
            
            time.sleep(1)
        
        raise PlatformNotReadyError("search result cards did not appear within 60s")

12. self._run_with_page(action_name, action)
    → platforms/goofish_playwright.py:520-647

    def _run_with_page(self, action_name, action):
        # 使用browser_worker在后台线程执行
        worker = get_browser_worker()
        
        def execute_on_worker():
            return self.browser_manager.with_page(action_name, action)
        
        return worker.execute(execute_on_worker)
```

**browser_manager执行**: `platforms/browser_manager.py:144-160`

```
13. browser_manager.with_page(action_name, callback)
    → platforms/browser_manager.py:144-160

    def with_page(self, action_name, callback):
        with self._lock:
            context = self._ensure_started_locked(action_name)  # ← 确保browser启动
            page = self._ensure_page_locked(context)  # ← 获取page对象
            try:
                return callback(page)  # ← 执行action函数
            finally:
                self._save_storage_state_locked()  # ← 保存状态

14. _ensure_started_locked(action_name)
    → platforms/browser_manager.py:174-265

    def _ensure_started_locked(self, action_name):
        # 检查context是否已启动
        if self._context_is_live_locked():
            logger.info("reusing Goofish browser context: context_id=%s", id(self._context))
            return self._context  # ← 复用现有context
        
        # 启动新browser
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,  # ← 使用初始化时的headless值
            slow_mo=self.slow_mo_ms,
        )
        
        # ✅ 加载storage_state到新context
        self._restore_storage_state_locked()
        
        logger.info("Goofish browser context started: context_id=%s", id(self._context))
        return self._context

15. _restore_storage_state_locked()
    → platforms/browser_manager.py:477-517

    def _restore_storage_state_locked(self):
        if self._context is None or not self.storage_state_path.exists():
            return
        
        # 读取goofish.json
        state = json.loads(self.storage_state_path.read_text(encoding="utf-8"))
        
        # 恢复cookies
        cookies = state.get("cookies") or []
        if cookies:
            self._context.add_cookies(cookies)  # ← Playwright API
            logger.info("restored %s Goofish cookies", len(cookies))
        
        # 恢复localStorage
        for origin_state in state.get("origins") or []:
            local_storage = origin_state.get("localStorage") or []
            if local_storage:
                self._context.add_init_script(f"localStorage.setItem(...)")
```

**读取状态文件**:
- `data/browser_state/goofish.json` (读取cookies和localStorage)

**写入数据库**:
- `tools/xianyu.py:170` → `persist_competitor_observations(results)`
- 写入 `data/platform.db` 表 `competitor_observations`

---

## 流程5：前端展示结果

**前端**: `web/main.js:197-214`

```
16. 返回到前端
    ← api/xianyu.py:1495 返回JSON
    ← main.js:190 接收data对象

17. 渲染竞品列表
    → main.js:197-214

    if (data.items && data.items.length > 0) {
        const avgPrice = data.items.reduce(...) / data.items.length;
        resultArea.innerHTML = `
            <h3>竞品分析结果</h3>
            <div>平均价格: ¥${avgPrice} | 样本数量: ${data.items.length}</div>
            ${data.items.map(item => `
                <div class="listing-card">
                    <div class="listing-title">${item.title}</div>
                    <div class="listing-price">¥${item.price}</div>
                </div>
            `).join('')}
        `;
    }
```

---

## 流程总结

**关键状态文件**:
1. `data/browser_state/goofish.json` - 登录态（cookies + localStorage）
2. `data/browser_state/goofish_profile/` - Chromium用户数据目录
3. `data/platform.db` - 数据库（竞品数据、审批等）

**关键单例**:
1. `_goofish_browser_manager` (全局) - `platforms/browser_manager.py:557`
2. `_browser_worker` (全局) - `platforms/browser_worker.py:82-89`

**登录态判断位置**:
1. **前端展示**: `main.js:351` 检查 `data.storage_state_exists`
2. **后端API**: `api/xianyu.py:125` 检查文件 `storage_state.exists()`
3. **后端状态**: `api/xianyu.py:296-319` `_stored_login_state()` 检查文件完整性
4. **抓取前检查**: `platforms/goofish_playwright.py:283` 检查文件是否存在
5. **抓取中检查**: `platforms/goofish_playwright.py:305, 1127` 检查页面是否显示"登录"按钮

**context复用机制**:
- `browser_manager` 是全局单例
- 一旦启动，`_context` 保存在内存中
- 除非显式调用 `stop()` 或 `shutdown_goofish_browser_manager()`，否则一直复用
- 复用时，`_restore_storage_state_locked()` **不会重新调用**
- 只有在 `_context is None` 时才重新启动并加载 `goofish.json`
