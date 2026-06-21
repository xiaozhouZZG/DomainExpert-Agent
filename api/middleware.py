"""Middleware helpers."""

from __future__ import annotations

import time

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from config import settings


class AuthMiddleware(BaseHTTPMiddleware):
    """Simple token auth middleware."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ["/", "/api/health"]:
            return await call_next(request)

        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        expected_token = settings.default_token
        if not expected_token:
            raise HTTPException(status_code=503, detail="DEFAULT_TOKEN 未配置")
        if not token or token != expected_token:
            raise HTTPException(status_code=401, detail="未授权")

        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Request logging middleware."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time
        print(f"{request.method} {request.url.path} - {response.status_code} ({duration:.3f}s)")
        return response
