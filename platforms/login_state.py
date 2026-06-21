"""
登录态单一真相源

只由真实事件更新：
1. 登录成功 → logged_in=True
2. 抓取中检测到未登录 → logged_in=False
3. 抓取成功 → logged_in=True
"""
import threading
import time
from typing import Dict, Any

# 全局登录态缓存（单一真相源）
_login_state_cache: Dict[str, Any] = {
    "logged_in": False,
    "checked_at": 0,  # 时间戳
    "detail": "未检查",
}

_cache_lock = threading.RLock()


def update_login_state(logged_in: bool, detail: str) -> None:
    """更新登录态（只由真实事件调用）"""
    with _cache_lock:
        _login_state_cache["logged_in"] = logged_in
        _login_state_cache["checked_at"] = time.time()
        _login_state_cache["detail"] = detail


def get_login_state() -> Dict[str, Any]:
    """获取当前登录态（单一真相源）"""
    with _cache_lock:
        return _login_state_cache.copy()


def mark_login_success(detail: str = "登录成功") -> None:
    """标记登录成功"""
    update_login_state(True, detail)


def mark_not_logged_in(detail: str = "未登录") -> None:
    """标记未登录"""
    update_login_state(False, detail)


def mark_fetch_success(detail: str = "抓取成功，已登录") -> None:
    """标记抓取成功（说明登录态有效）"""
    update_login_state(True, detail)
