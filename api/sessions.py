"""
会话管理 API

提供会话的增删改查功能
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from database.connection import get_db_connection
import json
import uuid

router = APIRouter()


class Message(BaseModel):
    role: str
    content: str
    agent: Optional[str] = None
    trace: Optional[dict] = None
    created_at: Optional[str] = None


class Session(BaseModel):
    session_id: str
    title: str
    user_id: str
    created_at: str
    updated_at: str


class CreateSessionRequest(BaseModel):
    user_id: str = "default_user"


class SessionMessagesResponse(BaseModel):
    session_id: str
    title: str
    messages: List[Message]


@router.post("/sessions")
async def create_session(request: CreateSessionRequest):
    """创建新会话"""
    session_id = f"session_{uuid.uuid4().hex[:16]}"
    now = datetime.now().isoformat()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO conversation_sessions (session_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, request.user_id, "新对话", now, now))

        conn.commit()

        return {
            "session_id": session_id,
            "title": "新对话",
            "user_id": request.user_id,
            "created_at": now,
            "updated_at": now
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"创建会话失败: {str(e)}")
    finally:
        conn.close()


@router.get("/sessions")
async def list_sessions(user_id: str = "default_user"):
    """获取用户的会话列表"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT session_id, title, created_at, updated_at
            FROM conversation_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
        """, (user_id,))

        rows = cursor.fetchall()

        sessions = []
        for row in rows:
            sessions.append({
                "session_id": row[0],
                "title": row[1] or "新对话",
                "created_at": row[2],
                "updated_at": row[3]
            })

        return sessions
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取会话列表失败: {str(e)}")
    finally:
        conn.close()


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """获取会话的所有消息"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 获取会话信息
        cursor.execute("""
            SELECT title FROM conversation_sessions
            WHERE session_id = ?
        """, (session_id,))

        session_row = cursor.fetchone()
        if not session_row:
            raise HTTPException(status_code=404, detail="会话不存在")

        # 获取消息列表
        cursor.execute("""
            SELECT role, content, agent, trace_data, created_at
            FROM conversations
            WHERE session_id = ?
            ORDER BY created_at ASC
        """, (session_id,))

        rows = cursor.fetchall()

        messages = []
        for row in rows:
            msg = {
                "role": row[0],
                "content": row[1],
                "agent": row[2],
                "created_at": row[4]
            }

            # 解析 trace_data
            if row[3]:
                try:
                    msg["trace"] = json.loads(row[3])
                except:
                    msg["trace"] = None
            else:
                msg["trace"] = None

            messages.append(msg)

        return {
            "session_id": session_id,
            "title": session_row[0] or "新对话",
            "messages": messages
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取消息失败: {str(e)}")
    finally:
        conn.close()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 删除消息
        cursor.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))

        # 删除会话
        cursor.execute("DELETE FROM conversation_sessions WHERE session_id = ?", (session_id,))

        conn.commit()

        return {"status": "success", "message": "会话已删除"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"删除会话失败: {str(e)}")
    finally:
        conn.close()


@router.patch("/sessions/{session_id}/title")
async def update_session_title(session_id: str, title: str):
    """更新会话标题"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE conversation_sessions
            SET title = ?, updated_at = ?
            WHERE session_id = ?
        """, (title, datetime.now().isoformat(), session_id))

        conn.commit()

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="会话不存在")

        return {"status": "success", "message": "标题已更新"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"更新标题失败: {str(e)}")
    finally:
        conn.close()

