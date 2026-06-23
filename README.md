# DomainExpert-Agent

## 闲鱼智能客服与自动化运营平台

面向闲鱼运营场景的企业级智能客服系统。买家发消息后，系统自动识别问题、检索商品知识库、生成安全回复，并在低置信度或敏感场景下转人工处理。

---

## 核心功能

### 闲鱼自动化

- **扫码登录**：网页一键扫码，浏览器会话持久化
- **自动读取消息**：后台轮询未读买家消息
- **RAG 商品问答**：上传商品知识库，自动检索匹配
- **三段式护栏**：高置信度自动回复，灰区/未匹配转人工
- **敏感问题拦截**：价格谈判、发货承诺等敏感话题转人工
- **白名单保护**：默认仅对授权测试账号自动回复，支持灰度放量

### 网页工作台

- **智能客服工作台**：自动客服开关、消息流、人工接管区
- **浏览器锁释放**：一键清理残留进程，恢复浏览器会话
- **竞品分析**：闲鱼同类商品价格分析
- **商品管理**：商品资料读取与管理
- **知识库管理**：上传商品问答文档

### 错误恢复

- 登录失效提示 + 扫码恢复
- Profile lock 检测 + 一键释放
- 网络错误退避重试，不死循环

---

## 技术架构

```
Web 智能客服工作台
        ↓
   FastAPI (8802)
        ↓
Auto Reply Orchestrator (后台循环)
        ↓
RAG Retrieval Gateway (三段式护栏)
        ↓
   Browser Worker (单线程隔离)
        ↓
Playwright Sync API (闲鱼自动化)
```

---

## 技术亮点

### Browser Worker 单线程隔离

Playwright Sync API 与 FastAPI async 有事件循环冲突。本项目通过 `BrowserWorker` 单线程隔离，所有 Playwright 操作通过 `worker.execute()` 执行，解决 asyncio 线程安全问题。

### Profile Lock 检测与释放

浏览器 profile 被旧进程占用时会检测并提示，提供一键释放按钮，用 psutil 终止残留 Chrome 进程并清理锁文件。

### RAG 三段式护栏

- **high (>=0.60)**：自动生成回复
- **gray (0.53-0.60)**：转人工处理
- **not_found (<0.53)**：无可靠答案，转人工

避免模型瞎编商品价格、发货方式、售后承诺。

### 转人工状态机

```
open/bot → pending_handoff → human_taking → resolved
```

会话状态持久化，支持人工接管后返回机器人。

### 自动回复白名单保护

只对白名单测试账号（如"海王星上蹿下跳的豆浆"）真发。真实买家消息只入库，不自动回复，需人工确认。

### 发送后硬校验

发送回复后检查 DOM 气泡是否包含发送内容，确保真实送达，不谎报成功。

### 网络错误退避

网络/超时错误退避重试 2s/4s/8s，最多 3 次，失败放弃本轮，不拖死整个服务。

---

## 启动方式

```bash
# 启动服务（唯一入口，端口 8802）
python app.py
```

访问：
- **智能客服工作台**：http://localhost:8802/
- **后台管理页**：http://localhost:8802/admin

不允许其他入口（如 `python api/server.py`、端口 8000）。

---

## 安全与风控设计

DomainExpert-Agent 在自动化触达买家前加入了多层风控机制，避免模型误答、误发和越权操作。

- **白名单与灰度策略**：自动回复能力默认仅对授权测试账号或灰度账号启用，便于分阶段验证和逐步放量。
- **人工接管机制**：价格协商、退款、催发货、售后争议、低置信度问题会自动进入人工处理区，避免机器人越权处理。
- **RAG 置信度护栏**：系统基于知识库命中分数进行 high / gray / not_found 三段式决策，只有高置信度内容才进入自动回复流程。
- **发送后硬校验**：回复发送后会校验闲鱼页面中的消息气泡，降低"接口成功但页面未发送"的假成功风险。
- **登录态与浏览器会话保护**：浏览器 Profile、Cookie、localStorage、数据库、日志等运行态文件均不进入仓库。
- **密钥与配置隔离**：模型 API Key 通过本地环境变量或 `.env` 注入，仓库仅保留 `.env.example`。

---

## 快速体验

1. 启动服务：

```bash
python app.py
```

2. 打开 Web 工作台：http://localhost:8802/

3. 在智能客服工作台完成闲鱼扫码登录。

4. 在知识库管理中导入商品问答、发货说明、售后规则等资料。

5. 开启自动客服开关。

6. 使用授权测试账号发送买家问题，例如"这个怎么卖""怎么发货"。

7. 系统会自动完成消息识别、知识库检索、回复生成、发送校验等流程。

8. 低置信度或敏感问题会进入人工接管区，由运营人员继续处理。

---

## 项目结构

```
app.py                          # 唯一入口，端口 8802
config.py                       # 配置管理
CLAUDE.md                       # 项目宪法

core/
  auto_reply_orchestrator.py    # 后台常驻循环
  auto_reply_logic.py           # 业务决策纯函数
  auto_reply_adapter.py         # 闲鱼接入层
  conversation_status.py        # 会话状态机
  xianyu_service.py             # 消息入库服务

platforms/
  browser_manager.py            # 浏览器单例管理
  browser_worker.py             # 单线程隔离
  goofish_playwright.py         # 闲鱼自动化

knowledge/
  retrieval_gateway.py          # 统一检索出口
  hybrid_rag_engine.py          # 混合检索引擎

api/
  admin.py                      # 自动客服 API
  xianyu.py                     # 闲鱼登录/消息 API
  knowledge.py                  # 知识库管理 API

web/
  main.html                     # 智能客服工作台
  admin.html                    # 后台管理页
```

---

## License

MIT