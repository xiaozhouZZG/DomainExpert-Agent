"""
#41 行为测试: _one_cycle 非阻塞互斥锁。

契约(断言断到具体值):
- 并发: 一个线程进 body 持锁阻塞时, 第二个并发调 → status=="skipped_locked"; 放行后第一个 status=="ok"。
- release 生效: 第一个跑完后再调能正常拿锁(串行两次都 ok)。
- 串行不死锁: 连调 4 次(对应 test_should_bot_reply 的 4 次串行)每次 ok。

全程 monkeypatch get_unread_messages 返回空消息(body 快速走完不碰真库) + _set_config 置 no-op。
"""
import threading

import pytest

import core.auto_reply_adapter as adapter
from core import auto_reply_orchestrator as orch


@pytest.fixture(autouse=True)
def _ensure_lock_released():
    """每个用例结束后强制把 module 锁复位, 避免用例间污染。"""
    yield
    if orch._cycle_lock.locked():
        try:
            orch._cycle_lock.release()
        except RuntimeError:
            pass


def _patch_fast(monkeypatch, get_unread=None):
    if get_unread is None:
        get_unread = lambda: {"status": "ready", "messages": []}
    monkeypatch.setattr(adapter, "get_unread_messages", get_unread)
    monkeypatch.setattr(orch, "_set_config", lambda *a, **k: None)


def test_concurrent_second_call_skipped(monkeypatch):
    """并发: 线程1 进 body 持锁阻塞, 线程2 并发调 → skipped_locked。"""
    entered = threading.Event()
    release = threading.Event()

    def blocking_get_unread():
        entered.set()           # 线程1 已进入 body(已持锁)
        release.wait(5)         # 卡住, 保持持锁
        return {"status": "ready", "messages": []}

    _patch_fast(monkeypatch, get_unread=blocking_get_unread)

    results = {}

    def call(tag):
        results[tag] = orch._one_cycle()

    t1 = threading.Thread(target=call, args=("first",))
    t1.start()
    try:
        assert entered.wait(5), "线程1 未进入 body"
        t2 = threading.Thread(target=call, args=("second",))
        t2.start()
        t2.join(5)
        assert results["second"]["status"] == "skipped_locked"   # 拿不到锁 → 跳过
        release.set()                                            # 放行线程1
        t1.join(5)
        assert results["first"]["status"] == "ok"                # 线程1 正常完成
    finally:
        release.set()           # 即使前面 assert 失败也放行线程1, 释放锁
        t1.join(5)


def test_release_after_done(monkeypatch):
    """第一个跑完后再调能正常拿锁(证明 finally 真 release)。"""
    _patch_fast(monkeypatch)
    assert orch._one_cycle()["status"] == "ok"
    assert orch._one_cycle()["status"] == "ok"   # 锁已 release, 第二次能拿到


def test_serial_calls_no_deadlock(monkeypatch):
    """串行连调 4 次每次正常, 不死锁(普通 Lock 无重入, 但每次 acquire→finally release)。"""
    _patch_fast(monkeypatch)
    for _ in range(4):
        assert orch._one_cycle()["status"] == "ok"
