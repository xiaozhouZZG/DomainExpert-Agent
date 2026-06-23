# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

企业级闲鱼（Goofish）自动化运营平台，基于 FastAPI + LangGraph + RAG + Playwright。核心功能：智能客服自动回复、知识库检索、竞品分析、商品上架。

## 启动

```bash
python app.py                    # 端口 8802，拒绝其他端口
pytest tests/ -v                 # 运行测试
pytest tests/test_conversation_status.py -v  # 单个测试文件
```

## 架构

### 三层自动客服体系（核心）

| 层 | 文件 | 职责 | 依赖 |
|---|------|------|------|
| ① 业务层 | `core/auto_reply_logic.py` | `decide_reply(buyer_msg, conversation_id)` 纯函数，不碰浏览器、不写 DB | `knowledge.retrieval_gateway`, `core.config_manager` |
| ② 接入层 | `core/auto_reply_adapter.py` | `get_unread_messages()` / `send_reply()` — 薄封装 Playwright，带退避重试 2s/4s/8s | `platforms.goofish_playwright` |
| ③ 编排层 | `core/auto_reply_orchestrator.py` | 常驻循环 8~10s/轮，状态持久化到 `system_config` 表，消息流缓存 | ① + ② |

- `decide_reply` 决策三段式: **high (>=0.60)** → LLM 生成回复; **gray (0.53-0.60) / not_found (<0.53)** → handoff
- **碰钱/敏感意图**（讲价、退款、催发货等正则匹配）→ 直接 handoff，不发
- 测试白名单: 只对 `海王星上蹿下跳的豆浆` 真发，`approval_id="test"` 绕过审批
- 错误处理: `need_login` → 暂停循环、不重试; 网络/超时 → 退避 3 次后放弃本轮; 其他 → 记录放弃
- `app.py` lifespan 启动时检查 `auto_reply_enabled` 配置，自动恢复后台线程

### API 路由 (全部挂载在 app.py 的 FastAPI 实例，端口 8802)

| 路由组 | 文件 | 前缀 |
|--------|------|------|
| 自动客服 | `api/admin.py` | `/api/admin/auto-reply/start\|stop\|status\|feed` |
| 聊天 | `api/chat.py` | `/api/chat` |
| 知识库 | `api/knowledge.py` | `/api/kb/*` |
| 闲鱼 | `api/xianyu.py` | `/api/xianyu/*` |
| Dashboard | `api/dashboard.py` | `/api/admin/dashboard` |
| Web 页面 | `app.py` | `GET /` → `web/main.html`, `GET /admin` → `web/admin.html` |

### RAG 知识库

- `knowledge/hybrid_rag_engine.py`: 混合检索（FAISS HNSW 向量 + BM25）→ RRF 融合 → BGE CrossEncoder 重排
- `knowledge/retrieval_gateway.py`: `search_with_confidence()` 统一检索出口，带三段式阈值判定
- 数据表: `documents` → `chunks`（带 embedding BLOB）, `knowledge_base`（纯文本导入）
- Embedder: BGE-small-zh-v1.5 (512维)，本地加载
- 构建索引: `engine.build_index()` 从 `chunks` 表读取（有 `embedding IS NOT NULL` 过滤）

### 闲鱼平台接入

- `platforms/goofish_playwright.py`: Playwright 驱动的闲鱼适配器，所有浏览器操作走 `browser_worker` 单线程
- `platforms/browser_manager.py`: `BrowserManager` 单例 (`get_goofish_browser_manager()`)，持久化 profile，复用 context
- `platforms/browser_worker.py`: 单例 Worker 线程，`worker.execute(func)` 排队执行
- `poll_unread_conversations(on_unread_message=None)`: 导航到 `/im` → 遍历会话列表 → 检查未读角标 → 点进会话读最后一条消息 → 可选回调
- `send_reply(conversation_id, content, approval_id, target)`: 选会话 → 填输入框 → 发 → 硬校验（检查自己气泡是否包含发送内容）
- 铁律: 所有 Playwright 操作必须在 browser_worker 线程; sync Playwright，不 `asyncio.run`; Playwright 事件循环会阻塞 RAG 的 `asyncio.run_until_complete`（已在 `hybrid_rag_engine.py` 做了独立线程处理）

### 会话状态机 (`core/conversation_status.py`)

```
open/bot → pending_handoff → human_taking → resolved
```
- `mark_conversation_pending_handoff()`: 设 pending_handoff + 写 draft_reply
- `handoff_to_human()`: pending_handoff → human_taking（有状态校验）
- `resolve_conversation()`: human_taking → resolved
- `should_bot_reply()`: 只有 open/bot/None 状态可回复

### 数据库 (SQLite WAL)

- `database/connection.py`: `get_db_connection()` 连接工厂，`ensure_db_ready()` 建表
- `xianyu_conversations`: `conversation_id` (goofish:买家名), `buyer_name`, `status`, `last_intent`
- `xianyu_messages`: `message_id` (SHA256[:32]), `conversation_id`, `direction` (buyer/seller), `content`, `draft_reply`, `sent_status`
- `system_config`: key-value 持久化 (`auto_reply_enabled`, `auto_reply_status`, `auto_reply_last_scan`)
- `knowledge_base`: 纯文本上传表（旧格式）; `chunks` + `documents`: RAG 索引表（新格式）

### 配置 (`config.py`)

- `API_PORT`: 8802
- `LLM_PROTOCOL`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`: LLM 配置
- `VECTOR_BACKEND`: auto/faiss_hnsw/sqlite_bruteforce
- `EMBEDDING_MODEL_PATH`: BGE 模型路径

## 关键约束

- **唯一启动命令**: `python app.py`，端口 8802。禁止 `python api/server.py`、禁止 8000 端口
- **单浏览器所有权**: 整个项目只能通过 `BrowserManager` 单例 (`get_goofish_browser_manager()`) 启动浏览器，禁止直接 `launch_persistent_context`
- 浏览器操作不能在 asyncio.run 的上下文中执行（Playwright 事件循环冲突），已在 `hybrid_rag_engine.search()` 用独立线程处理
- 真实买家绝不自动回复，白名单挡死
- `app.py` 拒绝非 8802 端口启动
- 前端消息模块 = 智能客服工作台（`web/main.html` 第 47 行起），不要另建页面
- Profile 被占用时返回 `profile_locked` 错误码，前端显示"重启浏览器会话"按钮，调用 `POST /api/admin/browser/restart`
