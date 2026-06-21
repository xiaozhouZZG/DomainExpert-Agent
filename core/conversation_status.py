"""
会话状态管理 - 转人工闭环

状态机:
  open/bot → pending_handoff (检索灰区/无答案)
  pending_handoff → human_taking (人工接手)
  human_taking → resolved (已解决)
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from database.connection import get_db_connection


def mark_conversation_pending_handoff(
    conversation_id: str,
    reason: str,
    buyer_message: str,
    confidence_score: float,
    suggested_reply: str | None = None
) -> dict[str, Any]:
    """
    标记会话为待人工处理

    Args:
        conversation_id: 会话ID
        reason: 转人工原因 (gray/not_found)
        buyer_message: 触发转人工的买家消息
        confidence_score: 检索置信度分数
        suggested_reply: 建议的 holding 话术

    Returns:
        {'status': 'ok', 'conversation_status': 'pending_handoff'}
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # 更新会话状态
        cursor.execute("""
            UPDATE xianyu_conversations
            SET status = 'pending_handoff',
                updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
        """, (conversation_id,))

        if cursor.rowcount == 0:
            raise ValueError(f"Conversation {conversation_id} not found")

        # 记录转人工原因到最后一条买家消息
        # 使用 draft_reply 字段存储 holding 建议话术和元数据
        handoff_metadata = {
            "type": "handoff",
            "reason": reason,
            "confidence_score": confidence_score,
            "buyer_message": buyer_message,
            "suggested_reply": suggested_reply or "您好，这个问题我帮您确认一下，稍后回复您",
            "handoff_at": datetime.now().isoformat(),
        }

        # SQLite不支持UPDATE...ORDER BY，先查询最后一条消息的message_id
        cursor.execute("""
            SELECT message_id FROM xianyu_messages
            WHERE conversation_id = ?
              AND direction = 'buyer'
              AND content = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (conversation_id, buyer_message))

        row = cursor.fetchone()
        if row:
            cursor.execute("""
                UPDATE xianyu_messages
                SET draft_reply = ?
                WHERE message_id = ?
            """, (json.dumps(handoff_metadata, ensure_ascii=False), row[0]))

        conn.commit()

        return {
            "status": "ok",
            "conversation_status": "pending_handoff",
            "suggested_reply": handoff_metadata["suggested_reply"]
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def handoff_to_human(conversation_id: str) -> dict[str, Any]:
    """
    人工接手会话

    Args:
        conversation_id: 会话ID

    Returns:
        {'status': 'ok', 'conversation_status': 'human_taking'}
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # 检查当前状态
        cursor.execute("""
            SELECT status FROM xianyu_conversations
            WHERE conversation_id = ?
        """, (conversation_id,))
        row = cursor.fetchone()

        if not row:
            raise ValueError(f"Conversation {conversation_id} not found")

        current_status = row[0]

        # 只允许从 pending_handoff 转为 human_taking
        if current_status != 'pending_handoff':
            raise ValueError(
                f"Cannot handoff conversation with status '{current_status}'. "
                f"Expected 'pending_handoff'"
            )

        # 更新状态
        cursor.execute("""
            UPDATE xianyu_conversations
            SET status = 'human_taking',
                updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
        """, (conversation_id,))

        conn.commit()

        return {
            "status": "ok",
            "conversation_status": "human_taking"
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_conversation(conversation_id: str) -> dict[str, Any]:
    """
    标记会话已解决

    Args:
        conversation_id: 会话ID

    Returns:
        {'status': 'ok', 'conversation_status': 'resolved'}
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # 检查当前状态
        cursor.execute("""
            SELECT status FROM xianyu_conversations
            WHERE conversation_id = ?
        """, (conversation_id,))
        row = cursor.fetchone()

        if not row:
            raise ValueError(f"Conversation {conversation_id} not found")

        current_status = row[0]

        # 只允许从 human_taking 转为 resolved
        if current_status != 'human_taking':
            raise ValueError(
                f"Cannot resolve conversation with status '{current_status}'. "
                f"Expected 'human_taking'"
            )

        # 更新状态
        cursor.execute("""
            UPDATE xianyu_conversations
            SET status = 'resolved',
                updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
        """, (conversation_id,))

        conn.commit()

        return {
            "status": "ok",
            "conversation_status": "resolved"
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_conversation_status(conversation_id: str) -> str | None:
    """
    获取会话当前状态

    Args:
        conversation_id: 会话ID

    Returns:
        状态字符串，或 None（会话不存在）
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT status FROM xianyu_conversations
            WHERE conversation_id = ?
        """, (conversation_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def should_bot_reply(conversation_id: str) -> bool:
    """
    判断机器人是否应该回复此会话

    Args:
        conversation_id: 会话ID

    Returns:
        True: 机器人可以回复（状态为 open 或 bot 或 None）
        False: 机器人应该闭嘴（状态为 pending_handoff / human_taking / resolved）
    """
    status = get_conversation_status(conversation_id)

    # 允许机器人回复的状态
    bot_allowed_statuses = {None, 'open', 'bot'}

    return status in bot_allowed_statuses
