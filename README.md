# EnterpriseAgent - 企业级多智能体业务自动化平台

## 架构特性

- ✅ **LangGraph 引擎**: 基于 LangGraph 的智能体编排
- ✅ **向量 RAG**: 向量检索 + BGE Embedding + Reranker
- ✅ **多智能体协作**: Supervisor + 4个专家 Agent (客服/订单/分析/报表)
- ✅ **工具注册表**: 8个工具 (数据查询/通知/审批/代码执行/报表)
- ✅ **执行轨迹**: 完整的请求执行轨迹记录与可视化
- ✅ **知识图谱**: 三元组存储，补充结构化事实
- ✅ **可观测性**: 结构化日志 + 调用链追踪 + Token成本核算

## 快速部署（Docker）

### 1. 准备配置文件

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑配置（必须设置 LLM API Key）
vim .env
```

修改 `.env` 中的以下配置：

```bash
# LLM 配置（必填）
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4

# 其他配置已预设，通常不需要修改
```

### 2. 构建并启动服务

```bash
# 构建镜像
docker-compose build

# 启动服务（后台运行）
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 3. 访问服务

- **用户聊天页**: http://服务器IP:8802
- **后台管理页**: http://服务器IP:8802/admin
- **知识库管理**: http://服务器IP:8802/kb

### 4. 初始化数据（首次启动）

```bash
# 进入容器
docker exec -it enterprise-agent bash

# 运行种子脚本（初始化数据库和示例数据）
python seed.py

# 退出容器
exit
```

### 5. 管理服务

```bash
# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 重启服务
docker-compose restart

# 停止服务
docker-compose down

# 停止并删除数据
docker-compose down -v
```

## 数据持久化

以下目录通过 volume 挂载，数据持久化保存在宿主机：

```
./data/              # 数据库文件
./data/knowledge/    # 知识库文件
./.cache/            # 模型缓存（embedding/reranker）
```

容器重启后数据不会丢失。

## 生产环境配置建议

### 1. 安全配置

修改 `.env` 中的管理员密码：

```bash
ADMIN_PASSWORD=your-strong-password-here
```

### 2. 反向代理（Nginx）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8802;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 3. HTTPS 配置

使用 Let's Encrypt 配置 HTTPS：

```bash
# 安装 certbot
sudo apt-get install certbot python3-certbot-nginx

# 获取证书
sudo certbot --nginx -d your-domain.com
```

### 4. 备份数据

```bash
# 备份数据库和知识库
tar -czf backup-$(date +%Y%m%d).tar.gz ./data

# 定期备份（crontab）
0 2 * * * cd /path/to/project && tar -czf backup-$(date +\%Y\%m\%d).tar.gz ./data
```

## 开发环境部署

如果需要在本地开发环境运行（不使用 Docker）：

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
vim .env

# 3. 初始化数据库
python seed.py

# 4. 启动服务
python app.py

# 访问: http://localhost:8802
```

## 目录结构

```
.
├── core/                # 编排引擎
│   ├── adapter.py      # 引擎适配器
│   ├── langgraph_engine.py  # LangGraph 引擎
│   ├── response_engine.py   # 响应引擎
│   └── rag_handler.py       # RAG 处理器
├── agents/             # Agent 配置
├── tools/              # 工具层
│   ├── registry.py     # 工具注册表
│   ├── database.py     # 数据库查询工具
│   ├── notification.py # 通知工具
│   └── knowledge_search.py  # 知识检索工具
├── knowledge/          # 知识层
│   ├── rag_engine.py   # RAG 引擎
│   ├── retriever.py    # 检索器（向量）
│   ├── embedder.py     # 向量化器（BGE）
│   └── reranker.py     # 重排器
├── database/           # 数据库
│   ├── models.py       # 数据模型
│   └── connection.py   # 连接管理
├── api/                # FastAPI 接口
│   ├── chat.py         # 聊天接口
│   ├── admin.py        # 管理接口
│   └── knowledge.py    # 知识库接口
├── web/                # 前端页面
├── app.py              # 主应用
├── seed.py             # 种子数据
├── Dockerfile          # Docker 镜像
├── docker-compose.yml  # Docker 编排
└── requirements.txt    # 依赖列表
```

## API 接口

### 用户接口
- `GET /` - 聊天页面
- `POST /api/chat` - 发送消息

### 管理接口
- `GET /admin` - 后台管理页面
- `GET /api/admin/dashboard` - 仪表盘数据
- `GET /api/admin/execution-traces` - 执行轨迹列表
- `GET /api/admin/execution-traces/{trace_id}` - 轨迹详情
- `GET /api/admin/conversations` - 对话记录
- `PUT /api/admin/llm-config` - 更新 LLM 配置

### 知识库接口
- `GET /kb` - 知识库管理页面
- `POST /api/kb/upload` - 上传文档
- `GET /api/kb/list` - 文档列表
- `POST /api/kb/search` - 知识检索

## 常见问题

### 1. 容器启动失败

查看日志定位问题：
```bash
docker-compose logs -f
```

常见原因：
- 端口 8802 被占用
- `.env` 文件配置错误
- LLM API Key 未设置

### 2. 模型下载慢

容器内已配置 Hugging Face 镜像源（https://hf-mirror.com），如果下载仍然很慢，可以：

1. 手动下载模型到 `.cache` 目录
2. 使用代理

### 3. 数据丢失

确保 volume 挂载正确：
```bash
docker-compose ps
docker volume ls
```

### 4. 性能优化

- 增加容器内存限制（docker-compose.yml 中添加 `mem_limit`）
- 使用 GPU 加速（需要 nvidia-docker）

## License

MIT
