# Docker 部署清理与配置总结

## 一、清理内容

### 已删除的文件

**日志文件：**
- `server.log` - 运行日志（已删除）

**临时文件：**
- `verify_trace_feature.py` - 验证脚本（已删除）
- `__pycache__/` - Python 字节码缓存（已清理所有目录）

**旧的部署文件：**
- `deploy.sh` - 旧的部署脚本（已删除）
- `start.sh` - 旧的启动脚本（已删除）
- `enterprise-agent.service` - systemd 服务文件（已删除）
- `nginx.conf` - Nginx 配置（已删除）
- `DEPLOYMENT.md` - 旧的部署文档（已删除）
- `PRODUCTION_CHECKLIST.md` - 旧的检查清单（已删除）
- `TRACE_IMPLEMENTATION.md` - 实现文档（已删除）

**旧的数据库：**
- `enterprise_agent.db` - 空的旧数据库（已删除）
- `platform.db` - 已移动到 `data/` 目录

### 新增/更新的文件

**配置文件：**
- `.gitignore` - 新增，忽略日志、缓存、数据库等
- `.env.example` - 更新，新增 RAG_MODE、HF_ENDPOINT 等配置
- `.env` - 新增本地测试配置
- `config.py` - 更新，支持新的环境变量

**Docker 配置：**
- `Dockerfile` - 更新：
  - 基于 Python 3.11
  - 预下载 embedding 模型和 reranker 模型
  - 配置 HF_ENDPOINT 镜像源
  - 端口改为 8802
  
- `docker-compose.yml` - 更新：
  - 端口映射 8802:8802
  - 强制 ENGINE=langgraph, RAG_MODE=vector
  - 持久化 data、.cache 目录
  - restart: unless-stopped

**文档：**
- `README.md` - 完全重写，专注 Docker 部署

### 未删除但保留的文件

**有意义的代码和配置：**
- `seed.py` - 种子数据脚本（功能代码，保留）
- 所有 `agents/`、`api/`、`core/`、`tools/`、`knowledge/` 下的代码（核心功能，保留）

**废弃但暂未删除的文件（确认无引用后可删）：**
无，已全部清理。

## 二、Docker 配置详情

### 端口配置
- **对外端口**: 8802
- **容器内端口**: 8802

### 引擎配置（写死）
- **ENGINE**: langgraph（固定，不走 fallback）
- **RAG_MODE**: vector（固定，使用向量检索）

### 依赖状态
- 全量安装 requirements.txt（包括 LangGraph、LangChain、sentence-transformers）
- 预下载模型到镜像：
  - BAAI/bge-small-zh-v1.5（embedding）
  - BAAI/bge-reranker-base（reranker）

### 数据持久化
通过 volume 挂载以下目录：
- `./data:/app/data` - 数据库文件
- `./data/knowledge:/app/data/knowledge` - 知识库文件
- `./.cache:/app/.cache` - 模型缓存

### 健康检查
- 每 30 秒检查一次
- 启动后 60 秒开始检查
- 失败 3 次标记为 unhealthy

## 三、验证清单

### ✅ 已完成
1. ✅ 清理所有日志文件
2. ✅ 清理所有 __pycache__ 目录
3. ✅ 删除临时测试脚本
4. ✅ 删除旧的部署脚本
5. ✅ 删除旧的文档
6. ✅ 更新 .gitignore
7. ✅ 更新 .env.example
8. ✅ 更新 Dockerfile（端口 8802，预下载模型）
9. ✅ 更新 docker-compose.yml（强制 langgraph + vector）
10. ✅ 更新 README.md（Docker 部署说明）
11. ✅ 更新 config.py（支持新配置）
12. ✅ 创建 data 目录
13. ✅ 移动 platform.db 到 data/
14. ✅ 本地服务已在 8802 端口正常运行

### 待验证（Docker 部署）
- [ ] docker-compose build 成功
- [ ] docker-compose up -d 成功
- [ ] 容器内服务正常运行
- [ ] 访问 http://localhost:8802 正常
- [ ] 发送消息能正常对话（langgraph + vector RAG）
- [ ] 重启容器后数据不丢失

## 四、部署说明

### 本地开发环境
```bash
# 1. 配置环境变量
cp .env.example .env
vim .env  # 修改 LLM_API_KEY

# 2. 安装依赖
pip install -r requirements.txt

# 3. 初始化数据库
python seed.py

# 4. 启动服务
python app.py

# 访问: http://localhost:8802
```

### Docker 部署
```bash
# 1. 配置环境变量
cp .env.example .env
vim .env  # 修改 LLM_API_KEY

# 2. 构建镜像
docker-compose build

# 3. 启动服务
docker-compose up -d

# 4. 查看日志
docker-compose logs -f

# 5. 初始化数据库（首次启动）
docker exec -it enterprise-agent python seed.py

# 访问: http://服务器IP:8802
```

### 服务器部署（生产环境）
```bash
# 1. 上传项目到服务器
scp -r . user@server:/opt/enterprise-agent/

# 2. 登录服务器
ssh user@server

# 3. 进入项目目录
cd /opt/enterprise-agent

# 4. 配置环境变量
cp .env.example .env
vim .env  # 修改配置

# 5. 构建并启动
docker-compose build
docker-compose up -d

# 6. 初始化数据
docker exec -it enterprise-agent python seed.py

# 7. 查看日志
docker-compose logs -f

# 访问: http://服务器IP:8802
```

## 五、配置参数说明

### 必须配置
- `LLM_API_KEY` - LLM API 密钥（必填）
- `LLM_BASE_URL` - LLM API 地址
- `LLM_MODEL` - 使用的模型

### 可选配置
- `ADMIN_PASSWORD` - 后台管理密码（默认：admin123）
- `LOG_LEVEL` - 日志级别（默认：INFO）
- `EMBEDDING_MODEL` - Embedding 模型（默认：BAAI/bge-small-zh-v1.5）
- `RERANKER_MODEL` - Reranker 模型（默认：BAAI/bge-reranker-base）

### 固定配置（不可修改）
- `ENGINE=langgraph` - 强制使用 LangGraph 引擎
- `RAG_MODE=vector` - 强制使用向量检索
- `API_PORT=8802` - 服务端口

## 六、故障排查

### 容器启动失败
```bash
# 查看日志
docker-compose logs -f

# 常见原因：
# 1. 端口 8802 被占用 -> netstat -ano | findstr :8802
# 2. .env 配置错误 -> 检查 LLM_API_KEY
# 3. 内存不足 -> free -h
```

### 模型下载慢
```bash
# 已配置 HF 镜像源，如果仍然慢：
# 1. 检查网络连接
# 2. 手动下载模型到 .cache 目录
# 3. 使用代理
```

### 数据丢失
```bash
# 确认 volume 挂载正确
docker volume ls
docker-compose ps

# 检查宿主机目录
ls -la ./data
ls -la ./.cache
```

---

**部署完成后访问：**
- 用户聊天页：http://服务器IP:8802
- 后台管理：http://服务器IP:8802/admin
- 知识库管理：http://服务器IP:8802/kb
