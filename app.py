"""主应用入口"""
import sys
import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 导入配置
from config import settings

# 初始化数据库
from database.connection import ensure_tables
ensure_tables()

# 创建 FastAPI 应用
app = FastAPI(title="EnterpriseAgent", version="1.0.0")

# 注册请求日志中间件
from middleware.request_logger import RequestLoggerMiddleware
app.add_middleware(RequestLoggerMiddleware)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# 导入路由
from api.chat import router as chat_router
from api.knowledge import router as kb_router
from api.admin import router as admin_router
from api.sessions import router as sessions_router
from api.greeting import router as greeting_router

app.include_router(chat_router)
app.include_router(kb_router)
app.include_router(admin_router)
app.include_router(sessions_router, prefix="/api")
app.include_router(greeting_router)


@app.get("/")
async def root():
    """主页"""
    return FileResponse("web/index.html")


@app.get("/kb")
async def kb_page():
    """知识库管理页"""
    return FileResponse("web/kb.html")


@app.get("/admin")
async def admin_page():
    """后台管理页"""
    return FileResponse("web/admin.html")


if __name__ == "__main__":
    logger.info(f"启动服务: {settings.api_host}:{settings.api_port}")

    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower()
    )
