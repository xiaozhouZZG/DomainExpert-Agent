"""聊天接口 - 直接使用 LangGraph 引擎"""
import logging
import time
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uuid

from database.connection import get_db_connection
from core.langgraph_engine import LangGraphEngine
from core.orchestrator import AgentMessage

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str
    session_id: Optional[str] = None
    customer_id: str = "CUST001"  # 当前用户ID（测试默认CUST001）


class ChatResponse(BaseModel):
    """聊天响应"""
    content: str
    agent: str
    session_id: str
    trace_id: str
    engine: str = "langgraph"
    metadata: dict = {}


def generate_title_from_message(message: str) -> str:
    """从首条消息生成会话标题"""
    title = message.strip()[:20]
    if len(message) > 20:
        title += "..."
    return title


@router.post("/api/chat")
async def chat(request: ChatRequest):
    """聊天接口"""
    start_time = time.time()
    session_id = request.session_id or f"session_{uuid.uuid4().hex[:16]}"
    trace_id = str(uuid.uuid4())

    try:
        logger.info(f"[/api/chat] 收到请求: message={request.message[:50]}...")

        # 创建/获取会话
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            if not request.session_id:
                now = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO conversation_sessions (session_id, customer_id, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (session_id, request.customer_id, generate_title_from_message(request.message), now, now))
                logger.info(f"[/api/chat] 创建新会话: {session_id}")

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"创建会话失败: {e}")

        # 加载历史消息
        conversation_history = []
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT role, content FROM conversations
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT 20
            """, (session_id,))

            for row in cursor.fetchall():
                role, content = row
                conversation_history.append(AgentMessage(role=role, content=content))

            conn.close()
            logger.info(f"[/api/chat] 加载历史: {len(conversation_history)} 条")
        except Exception as e:
            logger.error(f"加载历史失败: {e}")

        # 保存用户消息
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO conversations (session_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
            """, (session_id, "user", request.message, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"保存用户消息失败: {e}")

        # 调用 LangGraph 引擎
        try:
            engine = LangGraphEngine()

            # 添加当前用户消息
            messages = conversation_history + [AgentMessage(role="user", content=request.message)]

            result = await engine.execute(
                messages=messages,
                session_id=session_id,
                customer_id=request.customer_id,
                config={}
            )

            response_text = result.get("final_response", "")
            agent_name = result.get("agent", "system")

            if not response_text.strip():
                raise ValueError("LangGraph 返回空回复")

        except Exception as e:
            logger.error(f"LangGraph 执行失败: {e}", exc_info=True)
            response_text = "抱歉，系统暂时无法处理您的请求。请稍后再试或联系人工客服。"
            agent_name = "system"

        # 保存 AI 回复
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO conversations (session_id, trace_id, role, content, agent, engine, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (session_id, trace_id, "assistant", response_text, agent_name, "langgraph", datetime.now().isoformat()))

            cursor.execute("""
                UPDATE conversation_sessions SET updated_at = ? WHERE session_id = ?
            """, (datetime.now().isoformat(), session_id))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"保存 AI 回复失败: {e}")

        latency_ms = (time.time() - start_time) * 1000
        logger.info(f"[/api/chat] 完成: agent={agent_name}, latency={latency_ms:.0f}ms")

        return ChatResponse(
            content=response_text,
            agent=agent_name,
            session_id=session_id,
            trace_id=trace_id,
            engine="langgraph",
            metadata={}
        )

    except Exception as e:
        logger.error(f"[/api/chat] 最外层异常: {e}", exc_info=True)
        return ChatResponse(
            content="抱歉，系统遇到问题。请稍后再试或联系人工客服。",
            agent="system",
            session_id=session_id,
            trace_id=trace_id,
            engine="error",
            metadata={"error": str(e)}
        )
