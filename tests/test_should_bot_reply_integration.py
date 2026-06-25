"""
R-07 行为测试:验证 _one_cycle 的状态守卫与 send_reply 调用关系。

行为契约:
1. should_bot_reply == False (pending_handoff/human_taking/resolved)
   → decide_reply 不被调用,send_reply 不被调用,feed 记 skipped
2. should_bot_reply == True (open/bot/None)
   → decide_reply 被调用一次
3. 同 message_id 已有 draft_reply → 整体跳过(decide_reply 也不调)

全 mock,不启 Playwright、不发真消息、不调真 LLM/RAG。
"""
from unittest.mock import patch, MagicMock

import pytest

from core import auto_reply_orchestrator as orch


@pytest.fixture
def fresh_feed():
    orch._message_feed.clear()
    yield
    orch._message_feed.clear()


def _patch_io(unread_messages):
    """统一 patch:get_unread + DB 去重 SELECT 永远 None + _save_buyer_message 跳过 + _add_to_feed 记录"""
    return [
        patch(
            "core.auto_reply_adapter.get_unread_messages",
            return_value={"status": "ok", "messages": unread_messages},
        ),
        patch("core.auto_reply_orchestrator._save_buyer_message"),
        patch("core.auto_reply_orchestrator._mark_handoff"),
        patch("core.auto_reply_orchestrator._save_sent_reply"),
    ]


def test_pending_handoff_skips_decide_and_send(fresh_feed):
    """pending_handoff:不调 decide_reply、不调 send_reply,记 feed=skipped"""
    msgs = [{"buyer_name": "B1", "last_buyer_msg": "hi"}]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.fetchone.return_value = None  # 去重 select → 未处理过

    with patch("core.auto_reply_orchestrator.get_db_connection", return_value=fake_conn), \
         patch("core.auto_reply_orchestrator.should_bot_reply", return_value=False) as p_guard, \
         patch("core.auto_reply_logic.decide_reply") as p_decide, \
         patch("core.auto_reply_adapter.send_reply") as p_send, \
         patch(
             "core.auto_reply_adapter.get_unread_messages",
             return_value={"status": "ok", "messages": msgs},
         ), \
         patch("core.auto_reply_orchestrator._save_buyer_message"):
        result = orch._one_cycle()

    assert p_guard.call_count == 1
    assert p_decide.call_count == 0, "状态守卫不允许时 decide_reply 必须不被调用"
    assert p_send.call_count == 0, "状态守卫不允许时 send_reply 必须不被调用"
    assert result["scanned"] == 1
    assert result["processed"] == 1
    assert any(e.get("action") == "skipped" for e in orch._message_feed)


def test_human_taking_skips_decide_and_send(fresh_feed):
    msgs = [{"buyer_name": "B2", "last_buyer_msg": "hello"}]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.fetchone.return_value = None

    with patch("core.auto_reply_orchestrator.get_db_connection", return_value=fake_conn), \
         patch("core.auto_reply_orchestrator.should_bot_reply", return_value=False), \
         patch("core.auto_reply_logic.decide_reply") as p_decide, \
         patch("core.auto_reply_adapter.send_reply") as p_send, \
         patch(
             "core.auto_reply_adapter.get_unread_messages",
             return_value={"status": "ok", "messages": msgs},
         ), \
         patch("core.auto_reply_orchestrator._save_buyer_message"):
        orch._one_cycle()

    assert p_decide.call_count == 0
    assert p_send.call_count == 0


def test_open_status_calls_decide_reply(fresh_feed):
    """should_bot_reply=True 时 decide_reply 必须被调用一次"""
    msgs = [{"buyer_name": "B3", "last_buyer_msg": "你好"}]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.fetchone.return_value = None

    with patch("core.auto_reply_orchestrator.get_db_connection", return_value=fake_conn), \
         patch("core.auto_reply_orchestrator.should_bot_reply", return_value=True), \
         patch(
             "core.auto_reply_logic.decide_reply",
             return_value={"action": "handoff", "text": "", "score": 0.0, "reason": "low"},
         ) as p_decide, \
         patch("core.auto_reply_adapter.send_reply") as p_send, \
         patch(
             "core.auto_reply_adapter.get_unread_messages",
             return_value={"status": "ok", "messages": msgs},
         ), \
         patch("core.auto_reply_orchestrator._save_buyer_message"), \
         patch("core.auto_reply_orchestrator._mark_handoff"):
        orch._one_cycle()

    assert p_decide.call_count == 1, "open/bot 状态下 decide_reply 必须被调用"
    assert p_send.call_count == 0, "decide_reply=handoff 时不应 send"


def test_dedup_skips_when_message_already_processed(fresh_feed):
    """同 message_id 已有 draft_reply → 整轮跳过(连 decide_reply 都不调)"""
    msgs = [{"buyer_name": "B4", "last_buyer_msg": "重复消息"}]
    fake_conn = MagicMock()
    # 去重 SELECT 返回 (1,) → 已处理
    fake_conn.cursor.return_value.fetchone.return_value = (1,)

    with patch("core.auto_reply_orchestrator.get_db_connection", return_value=fake_conn), \
         patch("core.auto_reply_orchestrator.should_bot_reply", return_value=True) as p_guard, \
         patch("core.auto_reply_logic.decide_reply") as p_decide, \
         patch("core.auto_reply_adapter.send_reply") as p_send, \
         patch(
             "core.auto_reply_adapter.get_unread_messages",
             return_value={"status": "ok", "messages": msgs},
         ), \
         patch("core.auto_reply_orchestrator._save_buyer_message") as p_save:
        orch._one_cycle()

    assert p_decide.call_count == 0, "已处理消息不该再 decide"
    assert p_send.call_count == 0, "已处理消息不该再 send"
    assert p_save.call_count == 0, "已处理消息不该再 save_buyer"
    assert p_guard.call_count == 0, "已处理消息在去重之前就 continue 了"
