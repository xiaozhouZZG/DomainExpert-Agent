"""
自动回复编排器 — 常驻循环 + 状态持久化

后台常驻循环：开启后每 8~10 秒
  ensure_im_ready → get_unread → decide_reply → send → 入库

状态持久化 (system_config):
  - auto_reply_enabled: "true"/"false"
  - auto_reply_status: "running"/"stopped"/"need_login"
  - auto_reply_last_scan: ISO时间戳

need_login 时自动暂停并记录。
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from datetime import datetime
from typing import Any, Optional

from database.connection import get_db_connection

logger = logging.getLogger(__name__)

# 消息流缓存（最近 50 条）
_message_feed: list[dict] = []
_FEED_MAX = 50


def _conversation_id_for(buyer_name: str) -> str:
    return f"goofish:{buyer_name}"


def _message_id_for(conversation_id: str, content: str, timestamp: str) -> str:
    payload = f"{conversation_id}\n{timestamp}\n{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _get_config(key: str, default: str = "") -> str:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def _set_config(key: str, value: str) -> None:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_config (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, (key, value))
        conn.commit()
    finally:
        conn.close()


def _add_to_feed(entry: dict) -> None:
    """添加到消息流缓存"""
    entry["timestamp"] = datetime.now().isoformat()
    _message_feed.append(entry)
    if len(_message_feed) > _FEED_MAX:
        _message_feed.pop(0)


def _save_buyer_message(message_id: str, conversation_id: str, buyer_name: str, content: str) -> None:
    """保存买家消息到 DB"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO xianyu_messages (message_id, conversation_id, direction, content, sent_status, created_at)
            VALUES (?, ?, 'buyer', ?, 'received', CURRENT_TIMESTAMP)
            ON CONFLICT(message_id) DO UPDATE SET content = excluded.content
        """, (message_id, conversation_id, content))
        cursor.execute("""
            INSERT INTO xianyu_conversations (conversation_id, buyer_name, platform, status, last_message_at, created_at, updated_at)
            VALUES (?, ?, 'goofish', 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(conversation_id) DO UPDATE SET last_message_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        """, (conversation_id, buyer_name))
        conn.commit()
    finally:
        conn.close()


def _save_sent_reply(message_id: str, conversation_id: str, reply_text: str, score: float) -> None:
    """保存已发回复到 DB"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        draft_data = {
            "type": "sent",
            "content": reply_text,
            "confidence_score": score,
            "sent_at": datetime.now().isoformat(),
        }
        cursor.execute(
            "UPDATE xianyu_messages SET draft_reply = ?, sent_status = 'sent' WHERE message_id = ?",
            (json.dumps(draft_data, ensure_ascii=False), message_id),
        )
        cursor.execute(
            "UPDATE xianyu_conversations SET status = 'bot', last_intent = 'auto_reply', updated_at = CURRENT_TIMESTAMP WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_handoff(conversation_id: str, reason: str, buyer_msg: str, score: float) -> None:
    """标记转人工"""
    from core.conversation_status import mark_conversation_pending_handoff
    mark_conversation_pending_handoff(
        conversation_id=conversation_id,
        reason=reason,
        buyer_message=buyer_msg,
        confidence_score=score,
    )


# ==================== 编排循环 ====================

_worker_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _one_cycle() -> dict[str, Any]:
    """执行一个扫描周期"""
    from core.auto_reply_adapter import get_unread_messages, send_reply
    from core.auto_reply_logic import decide_reply

    result = {"scanned": 0, "processed": 0, "errors": []}

    # 1. 获取未读
    unread = get_unread_messages()
    status = unread.get("status", "error")

    if status == "need_login":
        _set_config("auto_reply_status", "need_login")
        logger.warning("[编排] 需要登录，自动暂停")
        return {"status": "need_login", "detail": "需要扫码登录"}

    if status == "error":
        logger.error(f"[编排] get_unread 失败: {unread.get('detail')}")
        return {"status": "error", "detail": unread.get("detail")}

    messages = unread.get("messages", [])
    result["scanned"] = len(messages)

    # 2. 逐条处理
    for msg in messages:
        buyer_name = msg.get("buyer_name", "")
        buyer_msg = msg.get("last_buyer_msg", "")
        conversation_id = _conversation_id_for(buyer_name)

        # 去重
        now_iso = datetime.now().isoformat()
        msg_id = _message_id_for(conversation_id, buyer_msg, now_iso)

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM xianyu_messages WHERE message_id = ? AND draft_reply IS NOT NULL", (msg_id,))
            if cursor.fetchone():
                logger.info(f"[编排] 跳过已处理: {buyer_name}")
                continue
        finally:
            conn.close()

        # 保存买家消息
        _save_buyer_message(msg_id, conversation_id, buyer_name, buyer_msg)

        # 业务决策
        decision = decide_reply(buyer_msg, conversation_id)
        action = decision.get("action", "handoff")
        reply_text = decision.get("text", "")
        score = decision.get("score", 0.0)
        reason = decision.get("reason", "")

        feed_entry = {
            "buyer_name": buyer_name,
            "buyer_msg": buyer_msg,
            "action": action,
            "score": score,
            "reason": reason,
        }

        if action == "handoff":
            _mark_handoff(conversation_id, reason, buyer_msg, score)
            feed_entry["reply"] = ""
            _add_to_feed(feed_entry)
            logger.info(f"[编排] {buyer_name} → handoff({reason})")
            result["processed"] += 1
            continue

        # 发送回复
        send_result = send_reply(conversation_id, buyer_name, reply_text)
        send_status = send_result.get("status", "failed")

        if send_status == "sent":
            _save_sent_reply(msg_id, conversation_id, reply_text, score)
            feed_entry["reply"] = reply_text
            _add_to_feed(feed_entry)
            logger.info(f"[编排] {buyer_name} → 发送成功: {reply_text[:50]}...")
            result["processed"] += 1
        elif send_status == "need_login":
            _set_config("auto_reply_status", "need_login")
            return {"status": "need_login", "detail": "需要扫码登录"}
        else:
            _mark_handoff(conversation_id, f"send_failed:{send_status}", buyer_msg, score)
            feed_entry["reply"] = f"发送失败: {send_result.get('detail', '')}"
            _add_to_feed(feed_entry)
            result["errors"].append(f"{buyer_name}: 发送失败")

    _set_config("auto_reply_last_scan", datetime.now().isoformat())
    return {"status": "ok", **result}


def _worker_loop() -> None:
    """后台常驻循环"""
    while not _stop_event.is_set():
        try:
            enabled = _get_config("auto_reply_enabled", "false") == "true"
            if not enabled:
                logger.debug("[编排] 未启用，跳过本轮")
                time.sleep(5)
                continue

            status = _get_config("auto_reply_status", "stopped")
            if status == "need_login":
                logger.debug("[编排] 状态为 need_login，跳过本轮")
                time.sleep(5)
                continue

            _set_config("auto_reply_status", "running")
            logger.info("[编排] 开始扫描周期")
            result = _one_cycle()
            logger.info(f"[编排] 扫描完成: {result}")

        except Exception as e:
            logger.error(f"[编排] 循环异常: {e}")

        # 随机 8~10 秒
        sleep_time = random.randint(8, 10)
        time.sleep(sleep_time)


def start_auto_reply() -> dict[str, Any]:
    """启动自动回复"""
    global _worker_thread, _stop_event

    current_status = _get_config("auto_reply_status", "stopped")
    if current_status == "need_login":
        return {"status": "error", "detail": "需要先扫码登录"}

    _set_config("auto_reply_enabled", "true")
    _set_config("auto_reply_status", "running")

    if _worker_thread is None or not _worker_thread.is_alive():
        _stop_event.clear()
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="auto-reply-worker")
        _worker_thread.start()
        logger.info("[编排] 后台线程已启动")

    return {"status": "ok", "message": "自动客服已开启"}


def stop_auto_reply() -> dict[str, Any]:
    """停止自动回复"""
    _set_config("auto_reply_enabled", "false")
    _set_config("auto_reply_status", "stopped")
    _stop_event.set()
    logger.info("[编排] 自动客服已停止")
    return {"status": "ok", "message": "自动客服已停止"}


def get_auto_reply_status() -> dict[str, Any]:
    """获取自动回复状态"""
    enabled = _get_config("auto_reply_enabled", "false") == "true"
    status = _get_config("auto_reply_status", "stopped")
    last_scan = _get_config("auto_reply_last_scan", "")

    return {
        "enabled": enabled,
        "status": status,
        "last_scan": last_scan,
        "message": {
            "running": "运行中",
            "stopped": "已停止",
            "need_login": "需扫码登录",
        }.get(status, status),
    }


def get_auto_reply_feed(limit: int = 20) -> list[dict]:
    """获取消息流"""
    return list(reversed(_message_feed[-limit:]))


def clear_need_login_status() -> dict[str, Any]:
    """清除 need_login 状态（扫码后调用）"""
    current = _get_config("auto_reply_status", "stopped")
    if current == "need_login":
        _set_config("auto_reply_status", "stopped")
        return {"status": "ok", "message": "已重置状态，可重新启动"}
    return {"status": "ok", "message": "状态无需重置"}
