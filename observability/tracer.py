"""调用链追踪"""
import uuid
from typing import Optional


class Tracer:
    """简单调用链追踪"""

    @staticmethod
    def generate_trace_id() -> str:
        """生成 trace ID"""
        return str(uuid.uuid4())

    @staticmethod
    def extract_trace_id(headers: dict) -> Optional[str]:
        """从请求头提取 trace ID"""
        return headers.get("X-Trace-Id")
