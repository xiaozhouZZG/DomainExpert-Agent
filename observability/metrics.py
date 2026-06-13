"""指标收集"""
from collections import defaultdict
from typing import Dict


class MetricsCollector:
    """简单指标收集器"""

    def __init__(self):
        self.counters: Dict[str, int] = defaultdict(int)
        self.gauges: Dict[str, float] = {}

    def increment(self, name: str, value: int = 1):
        """计数器递增"""
        self.counters[name] += value

    def set_gauge(self, name: str, value: float):
        """设置仪表值"""
        self.gauges[name] = value

    def get_metrics(self) -> dict:
        """获取所有指标"""
        return {
            "counters": dict(self.counters),
            "gauges": dict(self.gauges)
        }


# 全局实例
_metrics_collector = MetricsCollector()


def get_metrics() -> dict:
    """获取当前指标（全局函数）"""
    return _metrics_collector.get_metrics()


def increment_counter(name: str, value: int = 1):
    """递增计数器（全局函数）"""
    _metrics_collector.increment(name, value)


def set_gauge(name: str, value: float):
    """设置仪表（全局函数）"""
    _metrics_collector.set_gauge(name, value)

