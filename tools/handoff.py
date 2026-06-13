"""转人工工具"""
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import json
from datetime import datetime
import threading
import logging

from .registry import register_tool
from database.connection import get_db_connection

logger = logging.getLogger(__name__)


class HandoffInput(BaseModel):
    """转人工输入"""
    reason: str = Field(..., description="转人工原因（用户问题/无法解答的原因）")
    context: Optional[str] = Field(None, description="对话上下文摘要")


@register_tool("handoff_to_human")
@tool(args_schema=HandoffInput)
def handoff_to_human(reason: str, context: Optional[str] = None) -> str:
    """
    转接人工客服

    何时调用：
    - 工具和知识库都无法回答用户问题
    - 用户明确要求转人工（"转人工"、"人工客服"等）
    - 问题超出系统能力范围

    参数:
        reason: 转人工原因
        context: 对话上下文摘要（可选）

    返回:
        包含工单号的确认消息
    """
    # 获取当前用户
    current_user = getattr(threading.current_thread(), 'current_customer_id', 'UNKNOWN')
    session_id = getattr(threading.current_thread(), 'session_id', 'UNKNOWN')

    logger.info(f"【TOOL】handoff_to_human - customer={current_user}, reason={reason}")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO handoff_tickets (session_id, user_id, question, created_at, status, notes)
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (session_id, current_user, reason, now, context or ""))

        ticket_id = cursor.lastrowid
        conn.commit()

        logger.info(f"【转人工】工单已创建: ticket_id={ticket_id}, customer={current_user}")

        return json.dumps({
            "success": True,
            "ticket_id": ticket_id,
            "message": f"已为您转接人工客服，工单号 #{ticket_id}。客服人员将尽快与您联系，请稍候。",
            "customer_id": current_user
        }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"创建工单失败: {e}", exc_info=True)
        return json.dumps({
            "success": False,
            "error": f"转人工失败: {str(e)}"
        }, ensure_ascii=False)
    finally:
        if conn:
            conn.close()
