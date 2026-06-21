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
