# A. 核心流程地图

## 流程1：系统启动

**文件**: `app.py`

```
1. app.py:114-128
   if __name__ == "__main__":
       _assert_single_instance(bind_host, 8802)
       uvicorn.run(app, host=0.0.0.0, port=8802)

2. app.py:26
   ensure_db_ready()  # 初始化数据库

3. app.py:59
   app = FastAPI(lifespan=lifespan)

4. app.py:74-80
   # 注册所有路由
   app.include_router(chat_router)
   app.include_router(kb_router)
   app.include_router(admin_router)
   app.include_router(dashboard_router)
   app.include_router(sessions_router, prefix="/api")
   app.include_router(greeting_router)
   app.include_router(xianyu_router)

5. app.py:84-110
   # 注册页面路由
   GET / → web/main.html (主页)
   GET /chat → web/index.html (聊天页)
   GET /kb → web/kb.html (知识库)
   GET /admin → web/admin.html (管理后台)
   GET /xianyu-old → web/xianyu.html (旧闲鱼页)
   GET /dashboard-old → web/dashboard.html (旧数据页)
```

**状态文件**: 无

---

## 流程2：用户访问主页 → 点击"设置" → 查看登录状态

**前端**: `web/main.html` + `web/static/main.js`

```
1. 用户打开浏览器访问 http://localhost:8802
   → app.py:84 返回 web/main.html

2. main.html:209 加载 main.js
   <script src="/static/main.js"></script>

3. main.js:44-49 初始化导航
   initNavigation()
   document.querySelectorAll('.nav-item').forEach(item => {
       item.addEventListener('click', () => {
           const module = item.dataset.module;
           switchModule(module);  // ← 切换模块
       });
   });

4. 用户点击"⚙️ 设置"
   → main.html:37-40
   <li class="nav-item" data-module="settings">
   
   → main.js:51-71 switchModule('settings')
   → main.js:73-82 loadModuleData('settings')
   → main.js:345-373 loadSettings()

5. loadSettings() 调用API获取登录状态
   → main.js:348
   const data = await api('/api/xianyu/status');
   
   → main.js:351-357 显示状态
   if (data.storage_state_exists) {
       statusEl.textContent = '已登录';  // ← 显示"已登录"
       statusEl.className = 'status-badge success';
   } else {
       statusEl.textContent = '未登录';  // ← 显示"未登录"
       statusEl.className = 'status-badge error';
   }
```

**后端**: `api/xianyu.py`

```
6. GET /api/xianyu/status
   → api/xianyu.py:123-137

   @router.get("/status")
   async def get_xianyu_status():
       storage_state = Path("data/browser_state/goofish.json")
       profile_dir = Path("data/browser_state/goofish_profile")
       login_task = _public_login_task()
       login_state = _login_state_for_task(login_task)
       return {
           "storage_state_exists": storage_state.exists(),  # ← 只检查文件是否存在
           "login_state": login_state,
       }

7. _login_state_for_task(task)
   → api/xianyu.py:322-334

   def _login_state_for_task(task):
       state = _stored_login_state()  # ← 调用1
       result_state = (task.get("result") or {}).get("login_state") or {}
       if task.get("status") == "success" and result_state.get("logged_in"):
           state.update({"logged_in": True})  # ← 只在登录任务成功时改True
       return state

8. _stored_login_state()
   → api/xianyu.py:296-319

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
           "logged_in": stored_state_complete,  # ← 修复后：文件完整=True
           "status": "logged_in" if stored_state_complete else "not_logged_in",
       }
```

**读取状态文件**:
- `data/browser_state/goofish.json` (cookies + localStorage)

---

## 流程3：点击"扫码登录" → 弹出浏览器 → 扫码 → 保存登录态

**前端**: `web/main.html:183` + `web/static/main.js:375-402`

```
1. 用户点击"扫码登录"按钮
   → main.html:183
   <button class="btn-primary" onclick="startLogin()">扫码登录</button>

2. startLogin() 函数
   → main.js:375-402

   async function startLogin() {
       // 发起登录请求
       const data = await api('/api/xianyu/login/start', {
           method: 'POST',
           body: JSON.stringify({ max_wait_seconds: 300 })
       });
       
       alert('请在弹出的浏览器中扫码登录闲鱼');
       
       // 轮询登录状态（每2秒）
       const checkInterval = setInterval(async () => {
           const status = await api('/api/xianyu/login/status');
           if (status.login_state === 'completed') {
               clearInterval(checkInterval);
               alert('登录成功！');
               loadSettings();  // ← 刷新设置页
           } else if (status.login_state === 'failed') {
               clearInterval(checkInterval);
               alert('登录失败，请重试');
           }
       }, 2000);
   }
```

**后端**: `api/xianyu.py:140-170`

```
3. POST /api/xianyu/login/start
   → api/xianyu.py:140-170

   @router.post("/login/start")
   async def start_xianyu_login(req: LoginStartRequest):
       with _login_lock:
           if _login_task["running"]:
               return _public_login_task()  # ← 已有登录任务在跑
           
           _login_task.update({
               "running": True,
               "status": "running",
               "message": "Real browser opened locally."
           })
       
       # 在后台线程执行登录
       thread = threading.Thread(
           target=_run_login_task,
           args=(req.max_wait_seconds,),
           daemon=True,
       )
       thread.start()
       return _public_login_task()

4. _run_login_task(max_wait_seconds)
   → api/xianyu.py:255-283

   def _run_login_task(max_wait_seconds: int):
       from platforms.goofish_playwright import GoofishPlaywrightPlatform
       
       try:
           result = GoofishPlaywrightPlatform().login_interactive(
               max_wait_seconds=max_wait_seconds
           )
           with _login_lock:
               _login_task.update({
                   "running": False,
                   "status": "success",
                   "result": result,
               })
       except Exception as exc:
           with _login_lock:
               _login_task.update({
                   "running": False,
                   "status": "failed",
                   "error": str(exc),
               })
```

**浏览器操作**: `platforms/goofish_playwright.py:106-207`

```
5. GoofishPlaywrightPlatform().login_interactive()
   → platforms/goofish_playwright.py:106-207

   def login_interactive(self, max_wait_seconds: int = 300):
       started_at = time.time()
       
       # ✅ 删除旧的登录态文件
       if self.storage_state_path.exists():
           logger.warning("removing existing storage_state to force fresh login")
           self.storage_state_path.unlink()
       
       # ✅ 强制关闭旧browser_manager
       logger.info("stopping existing browser_manager to force fresh context")
       self.browser_manager.stop()
       
       # ✅ 设置headed模式（有界面）
       original_headless = self.browser_manager.headless
       self.browser_manager.headless = False
       
       try:
           def action(page):
               # 打开闲鱼首页
               self._goto_domcontentloaded_or_body(page, GOOFISH_URLS["home"])
               
               # 如果页面显示未登录，点击"登录"按钮
               self._open_login_prompt_if_needed(page)
               
               # 轮询检测登录状态（每5秒保存一次storage_state）
               while time.time() - started_at < max_wait_seconds:
                   if time.time() - last_save_at >= 5:
                       self.browser_manager.save_storage_state()  # ← 保存cookies
                       last_state = self._detect_login_state(page)  # ← 检测登录
                       
                       if last_state["logged_in"]:  # ← 检测到已登录
                           self._minimize_browser_window(page)
                           return {
                               "status": "success",
                               "login_state": last_state,
                           }
                   time.sleep(2)
               
               raise PlatformNotLoggedInError("timeout")
           
           result = self.browser_manager.with_page("login_interactive", action)
           
           # ✅ 登录成功后：清空全局单例
           logger.info("login completed, shutting down and clearing singleton")
           from platforms.browser_manager import shutdown_goofish_browser_manager
           shutdown_goofish_browser_manager()  # ← 清空全局单例
           
           return result
       except Exception as exc:
           self.browser_manager.headless = original_headless
           raise

6. self._detect_login_state(page)
   → platforms/goofish_playwright.py:946-981

   def _detect_login_state(self, page):
       storage_summary = summarize_storage_state(self.storage_state_path)
       logged_out = self._looks_logged_out(page)
       has_complete_state = bool(
           storage_summary.get("valid")
           and storage_summary.get("important_cookie_count", 0) >= 4
           and storage_summary.get("local_storage_item_count", 0) >= 1
           and storage_summary.get("size", 0) >= 20_000
       )
       
       if logged_out:
           return {"logged_in": False, "status": "not_logged_in"}
       
       if has_complete_state:
           return {"logged_in": True, "status": "logged_in"}  # ← 返回True
       
       return {"logged_in": False, "status": "not_logged_in"}

7. self._looks_logged_out(page)
   → platforms/goofish_playwright.py:1061-1105

   def _looks_logged_out(self, page):
       # 检查URL是否包含登录页标志
       if any(token in page.url.lower() for token in ("login.taobao.com", "passport", "login.htm")):
           return True
       
       # 检查是否有"个人中心"入口（已登录的标志）
       has_account_entry = page.evaluate(
           r"() => Array.from(document.querySelectorAll('a[href*=\"/personal\"]')).some(...)"
       )
       if has_account_entry:
           return False  # ← 有个人中心入口，说明已登录
       
       # 检查是否有"登录"按钮（未登录的标志）
       has_login_entry = page.evaluate(
           r"() => Array.from(document.querySelectorAll('a,button')).some((el) => el.text === '登录')"
       )
       if has_login_entry:
           return True  # ← 有登录按钮，说明未登录
       
       return False
```

**browser_manager保存状态**: `platforms/browser_manager.py:170-172, 443-472`

```
8. browser_manager.save_storage_state()
   → platforms/browser_manager.py:170-172

   def save_storage_state(self):
       with self._lock:
           self._save_storage_state_locked()

9. _save_storage_state_locked()
   → platforms/browser_manager.py:443-472

   def _save_storage_state_locked(self):
       if self._context is None:
           return
       
       tmp_path = self.storage_state_path.with_suffix(".tmp")
       
       # 保存当前context的cookies和localStorage
       self._context.storage_state(path=str(tmp_path))  # ← Playwright API
       
       # 检查新文件是否比旧文件更完整
       should_replace, reason = _should_replace_storage_state(
           self.storage_state_path, tmp_path
       )
       
       if not should_replace:
           logger.warning("skipped overwrite: %s", reason)
           tmp_path.unlink()
           return
       
       # 备份旧文件并替换
       if self.storage_state_path.exists():
           backup_path = self.storage_state_path.with_suffix(".bak")
           shutil.copy2(self.storage_state_path, backup_path)
       
       tmp_path.replace(self.storage_state_path)  # ← 保存成功

10. shutdown_goofish_browser_manager()
    → platforms/browser_manager.py:580-588

    def shutdown_goofish_browser_manager():
        global _goofish_browser_manager
        
        with _singleton_lock:
            manager = _goofish_browser_manager
            _goofish_browser_manager = None  # ← 清空全局单例
        
        if manager is not None:
            manager.stop()  # ← 关闭浏览器
```

**写入状态文件**:
- `data/browser_state/goofish.json` (cookies + localStorage)
- `data/browser_state/goofish.json.bak` (备份)

---
