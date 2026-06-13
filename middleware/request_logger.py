"""请求日志记录中间件"""
import time
import uuid
from datetime import datetime
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import logging

logger = logging.getLogger(__name__)

# 内存日志存储（最近100条）
_request_logs = []
MAX_LOGS = 100


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """记录请求到内存，供后台查看"""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/admin/logs"):
            # 跳过日志查询接口本身
            return await call_next(request)

        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # 记录请求
        log_entry = {
            "request_id": request_id,
            "timestamp": datetime.now().isoformat(),
            "method": request.method,
            "path": request.url.path,
            "status": None,
            "duration_ms": None,
            "error": None
        }

        try:
            response = await call_next(request)
            log_entry["status"] = response.status_code
            log_entry["duration_ms"] = int((time.time() - start_time) * 1000)

            # 只记录业务接口
            if request.url.path.startswith("/api/"):
                _add_log(log_entry)

            return response

        except Exception as e:
            log_entry["status"] = 500
            log_entry["error"] = str(e)
            log_entry["duration_ms"] = int((time.time() - start_time) * 1000)
            _add_log(log_entry)
            raise


def _add_log(entry: dict):
    """添加日志条目"""
    global _request_logs
    _request_logs.append(entry)
    if len(_request_logs) > MAX_LOGS:
        _request_logs.pop(0)


def get_recent_logs(limit: int = 50) -> list:
    """获取最近的日志"""
    return _request_logs[-limit:]
