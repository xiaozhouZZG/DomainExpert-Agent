# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

DomainExpert-Agent：面向闲鱼运营场景的企业级智能客服与自动化运营平台。基于 FastAPI + Playwright + LangGraph + RAG + SQLite。

## 铁律

- **唯一启动命令**：`python app.py`，端口 8802。禁止 `python api/server.py`、禁止 8000 端口
- **单浏览器所有权**：整个项目只能通过 `BrowserManager` 单例 (`get_goofish_browser_manager()`) 启动浏览器
- **所有 Playwright 操作必须走 browser_worker**：`worker.execute(func)`，FastAPI async 端点不能直接调用 Playwright sync API
- **前端消息模块 = 智能客服工作台**（`web/main.html`），不要另建页面
- **所有自动回复必须走白名单和护栏**：只对授权测试账号真发，非授权账号需人工接管
- **写代码前先 grep 现有功能**：能复用就复用，不能重复造
- **RAG 三段式护栏**：high 自动回复，gray/not_found 转人工。不允许模型瞎编价格、承诺

## 启动与测试

```bash
python app.py                                                    # 启动服务 (8802)
pytest tests/test_conversation_status.py -v                      # 状态机测试
pytest tests/test_retrieval_gateway.py -v                        # 检索测试
pytest tests/test_browser_worker.py -v                           # Worker 线程测试
```

## 核心架构

### 自动客服三层体系

| 层 | 文件 | 职责 |
|---|------|------|
| ① 业务层 | `core/auto_reply_logic.py` | `decide_reply()` 纯函数，检索+护栏+生成 |
| ② 接入层 | `core/auto_reply_adapter.py` | `get_unread_messages()` / `send_reply()` + 退避重试 |
| ③ 编排层 | `core/auto_reply_orchestrator.py` | 常驻循环 8-10s/轮，状态持久化，消息流 |

链路：网页开关 → 后台循环 → poll → decide_reply → send_reply / 转人工 → 消息流展示

### 浏览器架构（单线程隔离）

```
FastAPI async → worker.execute() → Playwright Sync API
```

- `platforms/browser_manager.py`：`BrowserManager` 单例，`with_page()` 通过 worker 执行
- `platforms/browser_worker.py`：单例 Worker 线程，`worker.execute(func)` 排队执行
- `platforms/goofish_playwright.py`：所有方法通过 `with_page()` / `_run_with_page()` 执行

### 会话状态机

```
open/bot → pending_handoff → human_taking → resolved
```

- `should_bot_reply()`：只有 open/bot 可回复
- `mark_conversation_pending_handoff()`：设 pending_handoff
- 状态持久化在 `xianyu_conversations` 表

### 错误处理

| 错误 | 处理 |
|------|------|
| need_login | 暂停循环，网页红字提示扫码 |
| profile_locked | 暂停循环，网页提示"释放浏览器锁"按钮 |
| 网络/超时 | 退避 2s/4s/8s 重试 3 次，放弃本轮 |
| 发送硬校验失败 | 记录失败，不谎报 |

### RAG 知识库

- `knowledge/retrieval_gateway.py`：`search_with_confidence()` 统一检索出口
- `knowledge/hybrid_rag_engine.py`：混合检索（FAISS HNSW + BM25）→ RRF → CrossEncoder 重排
- 三段式：high (>=0.60) → 自动回复；gray (0.53-0.60) / not_found (<0.53) → 转人工

### API 路由（全部 8802）

| 路由组 | 文件 | 前缀 |
|--------|------|------|
| 自动客服 | `api/admin.py` | `/api/admin/auto-reply/*`, `/api/admin/browser/*` |
| 闲鱼 | `api/xianyu.py` | `/api/xianyu/*` |
| 知识库 | `api/knowledge.py` | `/api/kb/*` |
| 聊天 | `api/chat.py` | `/api/chat` |

### 数据库 (SQLite WAL)

- `xianyu_conversations`：`conversation_id`, `buyer_name`, `status`, `last_intent`
- `xianyu_messages`：`message_id`, `conversation_id`, `direction`, `content`, `draft_reply`, `sent_status`
- `system_config`：key-value 持久化 (`auto_reply_enabled`, `auto_reply_status`)
- `chunks` + `documents`：RAG 索引表

### 配置 (`config.py`)

- `API_PORT`: 8802
- `LLM_PROTOCOL` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`：LLM 配置
- `VECTOR_BACKEND`: auto/faiss_hnsw/sqlite_bruteforce

## 重复代码注意

`core/shadow_pipeline.py` 有单次触发的自动回复逻辑（`/auto-reply` POST），与 `auto_reply_orchestrator.py` 的常驻循环有部分重复函数（`_conversation_id_for`, `_message_id_for`, `TEST_WHITELIST`, `SENSITIVE_INTENT_PATTERNS`）。修改时注意同步。
