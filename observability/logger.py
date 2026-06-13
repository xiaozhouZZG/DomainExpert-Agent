"""结构化日志"""
import logging
import json
from datetime import datetime


class StructuredLogger:
    """结构化 JSON 日志"""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def log(self, level: str, message: str, **kwargs):
        """记录结构化日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message,
            **kwargs
        }
        self.logger.info(json.dumps(log_entry, ensure_ascii=False))
