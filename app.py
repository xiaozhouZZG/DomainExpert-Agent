"""Application entrypoint."""

from __future__ import annotations

import logging
import socket
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from database.connection import ensure_db_ready
from platforms.browser_manager import shutdown_goofish_browser_manager
from platforms.browser_worker import shutdown_browser_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

ensure_db_ready()


def _check_pymupdf_availability():
    """启动时硬校验 PyMuPDF 依赖"""
    logger.info("=" * 60)
    logger.info("当前 Python 解释器: %s", sys.executable)
    logger.info("=" * 60)

    try:
        import fitz
        logger.info("✓ PyMuPDF 可用 (版本: %s)", fitz.__version__ if hasattr(fitz, '__version__') else 'unknown')
    except ImportError as e:
        logger.error("=" * 60)
        logger.error("✗ PyMuPDF 未安装！PDF 文档解析功能将不可用")
        logger.error("=" * 60)
        logger.error("请使用以下命令安装:")
        logger.error("    %s -m pip install pymupdf", sys.executable)
        logger.error("=" * 60)
        raise RuntimeError(
            f"缺少必需依赖 PyMuPDF。请运行: {sys.executable} -m pip install pymupdf"
        ) from e


_check_pymupdf_availability()


def _assert_single_instance(host: str, port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(f"已有实例在跑: {host}:{port}") from exc
    finally:
        probe.close()


def _enforce_single_port() -> None:
    argv = [arg.lower() for arg in sys.argv]
    if "--port" in argv:
        idx = argv.index("--port")
        configured = argv[idx + 1] if idx + 1 < len(argv) else ""
        if configured != "8802":
            raise RuntimeError(f"只允许使用 8802 端口启动，当前收到 --port {configured or '<missing>'}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    from knowledge.processing_queue import start_processing_queue
    start_processing_queue()

    # 自动回复循环：启动时检查是否之前开着，恢复运行
    try:
        from database.connection import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = 'auto_reply_enabled'")
        row = cursor.fetchone()
        enabled = row and row[0] == "true"

        if enabled:
            # 检查 profile 是否被占用
            from platforms.browser_manager import get_goofish_browser_manager
            mgr = get_goofish_browser_manager()
            if mgr.is_profile_locked():
                logger.warning("启动时检测到 profile 被其他进程占用，自动客服暂停")
                cursor.execute(
                    "INSERT INTO system_config (key, value, updated_at) VALUES ('auto_reply_status', 'profile_locked', CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value = 'profile_locked', updated_at = CURRENT_TIMESTAMP"
                )
                conn.commit()
            else:
                from core.auto_reply_orchestrator import start_auto_reply
                result = start_auto_reply()
                logger.info("自动客服恢复启动: %s", result)

        conn.close()
    except Exception as e:
        logger.warning("自动客服恢复启动失败（可能表不存在）: %s", e)

    try:
        yield
    finally:
        # 关闭时
        from core.auto_reply_orchestrator import stop_auto_reply
        stop_auto_reply()
        logger.info("自动客服已停止")

        shutdown_browser_worker()
        shutdown_goofish_browser_manager()

        from knowledge.processing_queue import stop_processing_queue
        stop_processing_queue()


_enforce_single_port()

app = FastAPI(title="EnterpriseAgent", version="1.0.0", lifespan=lifespan)

from middleware.request_logger import RequestLoggerMiddleware

app.add_middleware(RequestLoggerMiddleware)
app.mount("/static", StaticFiles(directory="web/static"), name="static")

from api.admin import router as admin_router
from api.chat import router as chat_router
from api.dashboard import router as dashboard_router
from api.knowledge import router as kb_router
from api.sessions import router as sessions_router
from api.xianyu import router as xianyu_router
from api.xianyu_dump import router as xianyu_dump_router

app.include_router(chat_router)
app.include_router(kb_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
app.include_router(sessions_router, prefix="/api")
app.include_router(xianyu_router)
app.include_router(xianyu_dump_router, prefix="/api/xianyu")


@app.get("/")
async def root():
    return FileResponse("web/main.html")


@app.get("/admin")
async def admin_page():
    return FileResponse("web/admin.html")


if __name__ == "__main__":
    settings.api_port = 8802
    bind_host = settings.api_host if settings.api_host not in {"0.0.0.0", "::"} else "127.0.0.1"
    try:
        _assert_single_instance(bind_host, settings.api_port)
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)

    logger.info("启动服务: %s:%s", settings.api_host, settings.api_port)
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
