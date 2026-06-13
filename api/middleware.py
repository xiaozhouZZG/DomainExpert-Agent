"""中间件（鉴权/限流/日志）"""
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import time


class AuthMiddleware(BaseHTTPMiddleware):
    """简单 Token 鉴权"""

    async def dispatch(self, request: Request, call_next):
        # 健康检查接口跳过鉴权
        if request.url.path in ["/", "/api/health"]:
            return await call_next(request)

        token = request.headers.get("Authorization", "").replace("Bearer ", "")

        # 简单校验（生产环境应使用 JWT）
        if not token or token != "demo-token":
            raise HTTPException(status_code=401, detail="未授权")

        response = await call_next(request)
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """请求日志"""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        response = await call_next(request)

        duration = time.time() - start_time
        print(f"{request.method} {request.url.path} - {response.status_code} ({duration:.3f}s)")

        return response
