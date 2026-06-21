"""
测试会话状态流转
"""
import pytest
from core.conversation_status import (
    mark_conversation_pending_handoff,
    handoff_to_human,
    resolve_conversation,
    get_conversation_status,
    should_bot_reply
)
from database.connection import get_db_connection


@pytest.fixture
def test_conversation():
    """创建测试会话"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 插入测试会话
    test_conv_id = "test_conv_status_flow"
    cursor.execute("""
        INSERT OR REPLACE INTO xianyu_conversations
        (conversation_id, buyer_name, platform, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (test_conv_id, "测试买家", "goofish", "open"))

    # 插入测试消息
    cursor.execute("""
        INSERT OR REPLACE INTO xianyu_messages
        (message_id, conversation_id, direction, content, created_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (f"{test_conv_id}_msg1", test_conv_id, "buyer", "测试消息"))

    conn.commit()
    conn.close()

    yield test_conv_id

    # 清理
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM xianyu_messages WHERE conversation_id = ?", (test_conv_id,))
    cursor.execute("DELETE FROM xianyu_conversations WHERE conversation_id = ?", (test_conv_id,))
    conn.commit()
    conn.close()


def test_initial_status(test_conversation):
    """测试初始状态"""
    status = get_conversation_status(test_conversation)
    assert status == "open"
    assert should_bot_reply(test_conversation) is True


def test_mark_pending_handoff(test_conversation):
    """测试标记为待人工处理"""
    result = mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="gray",
        buyer_message="测试消息",
        confidence_score=0.55,
        suggested_reply="您好，这个问题我需要确认一下"
    )

    assert result["status"] == "ok"
    assert result["conversation_status"] == "pending_handoff"

    # 验证状态已更新
    status = get_conversation_status(test_conversation)
    assert status == "pending_handoff"

    # 验证机器人应该闭嘴
    assert should_bot_reply(test_conversation) is False


def test_handoff_to_human(test_conversation):
    """测试人工接手"""
    # 先标记为待人工
    mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="not_found",
        buyer_message="测试消息",
        confidence_score=0.50
    )

    # 人工接手
    result = handoff_to_human(test_conversation)
    assert result["status"] == "ok"
    assert result["conversation_status"] == "human_taking"

    # 验证状态
    status = get_conversation_status(test_conversation)
    assert status == "human_taking"

    # 验证机器人仍然闭嘴
    assert should_bot_reply(test_conversation) is False


def test_resolve_conversation(test_conversation):
    """测试标记已解决"""
    # 准备状态链: open → pending_handoff → human_taking → resolved
    mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="gray",
        buyer_message="测试消息",
        confidence_score=0.56
    )
    handoff_to_human(test_conversation)

    # 标记已解决
    result = resolve_conversation(test_conversation)
    assert result["status"] == "ok"
    assert result["conversation_status"] == "resolved"

    # 验证状态
    status = get_conversation_status(test_conversation)
    assert status == "resolved"

    # 验证机器人仍然闭嘴
    assert should_bot_reply(test_conversation) is False


def test_invalid_state_transition(test_conversation):
    """测试非法状态转换"""
    # 尝试从 open 直接 resolve（应该失败）
    with pytest.raises(ValueError, match="Expected 'human_taking'"):
        resolve_conversation(test_conversation)

    # 尝试从 open 直接 handoff（应该失败）
    with pytest.raises(ValueError, match="Expected 'pending_handoff'"):
        handoff_to_human(test_conversation)


def test_bot_should_not_reply_when_handoff(test_conversation):
    """测试转人工后机器人不应回复"""
    # open 状态：机器人可以回复
    assert should_bot_reply(test_conversation) is True

    # 标记为待人工
    mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="gray",
        buyer_message="测试消息",
        confidence_score=0.55
    )

    # pending_handoff 状态：机器人不应回复
    assert should_bot_reply(test_conversation) is False

    # 人工接手
    handoff_to_human(test_conversation)

    # human_taking 状态：机器人仍不应回复
    assert should_bot_reply(test_conversation) is False


def test_nonexistent_conversation():
    """测试不存在的会话"""
    status = get_conversation_status("nonexistent_conv_id")
    assert status is None

    # 不存在的会话允许机器人回复（默认行为）
    assert should_bot_reply("nonexistent_conv_id") is True


def test_auto_wakeup_resolved_on_new_message(test_conversation):
    """测试 resolved 会话收到新消息自动唤醒"""
    from core.xianyu_service import ingest_buyer_messages

    # 准备：标记会话为 resolved
    mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="gray",
        buyer_message="测试消息",
        confidence_score=0.55
    )
    handoff_to_human(test_conversation)
    resolve_conversation(test_conversation)

    # 验证状态为 resolved
    assert get_conversation_status(test_conversation) == "resolved"
    assert should_bot_reply(test_conversation) is False

    # 收到新买家消息
    new_messages = [{
        "conversation_id": test_conversation,
        "buyer_name": "测试买家",
        "content": "新问题来了",
        "received_at": "2026-06-22T00:00:00"
    }]
    ingest_buyer_messages(new_messages)

    # 验证状态自动转回 open
    status = get_conversation_status(test_conversation)
    assert status == "open"

    # 验证机器人可以回复了
    assert should_bot_reply(test_conversation) is True


def test_no_wakeup_for_pending_handoff(test_conversation):
    """测试 pending_handoff 会话收到新消息不被唤醒"""
    from core.xianyu_service import ingest_buyer_messages

    # 标记为 pending_handoff
    mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="gray",
        buyer_message="测试消息",
        confidence_score=0.55
    )

    # 验证状态为 pending_handoff
    assert get_conversation_status(test_conversation) == "pending_handoff"

    # 收到新买家消息
    new_messages = [{
        "conversation_id": test_conversation,
        "buyer_name": "测试买家",
        "content": "又来一条消息",
        "received_at": "2026-06-22T00:00:00"
    }]
    ingest_buyer_messages(new_messages)

    # 验证状态仍然是 pending_handoff（不被唤醒）
    status = get_conversation_status(test_conversation)
    assert status == "pending_handoff"

    # 验证机器人仍然闭嘴
    assert should_bot_reply(test_conversation) is False


def test_no_wakeup_for_human_taking(test_conversation):
    """测试 human_taking 会话收到新消息不被唤醒"""
    from core.xianyu_service import ingest_buyer_messages

    # 标记为 human_taking
    mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="not_found",
        buyer_message="测试消息",
        confidence_score=0.50
    )
    handoff_to_human(test_conversation)

    # 验证状态为 human_taking
    assert get_conversation_status(test_conversation) == "human_taking"

    # 收到新买家消息
    new_messages = [{
        "conversation_id": test_conversation,
        "buyer_name": "测试买家",
        "content": "又来一条消息",
        "received_at": "2026-06-22T00:00:00"
    }]
    ingest_buyer_messages(new_messages)

    # 验证状态仍然是 human_taking（不被唤醒）
    status = get_conversation_status(test_conversation)
    assert status == "human_taking"

    # 验证机器人仍然闭嘴
    assert should_bot_reply(test_conversation) is False


def test_return_to_bot_from_human_taking(test_conversation):
    """测试从 human_taking 手动交回机器人"""
    from core.conversation_status import return_to_bot

    # 准备：标记为 human_taking
    mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="gray",
        buyer_message="测试消息",
        confidence_score=0.55
    )
    handoff_to_human(test_conversation)

    # 验证状态为 human_taking
    assert get_conversation_status(test_conversation) == "human_taking"

    # 手动交回机器人
    result = return_to_bot(test_conversation)
    assert result["status"] == "ok"
    assert result["conversation_status"] == "open"
    assert result["previous_status"] == "human_taking"

    # 验证状态已转回 open
    assert get_conversation_status(test_conversation) == "open"

    # 验证机器人可以回复了
    assert should_bot_reply(test_conversation) is True


def test_return_to_bot_from_resolved(test_conversation):
    """测试从 resolved 手动交回机器人"""
    from core.conversation_status import return_to_bot

    # 准备：标记为 resolved
    mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="not_found",
        buyer_message="测试消息",
        confidence_score=0.50
    )
    handoff_to_human(test_conversation)
    resolve_conversation(test_conversation)

    # 验证状态为 resolved
    assert get_conversation_status(test_conversation) == "resolved"

    # 手动交回机器人
    result = return_to_bot(test_conversation)
    assert result["status"] == "ok"
    assert result["conversation_status"] == "open"
    assert result["previous_status"] == "resolved"

    # 验证状态已转回 open
    assert get_conversation_status(test_conversation) == "open"

    # 验证机器人可以回复了
    assert should_bot_reply(test_conversation) is True


def test_cannot_return_to_bot_from_pending_handoff(test_conversation):
    """测试不能从 pending_handoff 交回机器人"""
    from core.conversation_status import return_to_bot

    # 标记为 pending_handoff
    mark_conversation_pending_handoff(
        conversation_id=test_conversation,
        reason="gray",
        buyer_message="测试消息",
        confidence_score=0.55
    )

    # 尝试交回机器人（应该失败）
    with pytest.raises(ValueError, match="Expected 'human_taking' or 'resolved'"):
        return_to_bot(test_conversation)
