"""Local Xianyu customer-service workflow state."""

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from database.connection import get_db_connection

logger = logging.getLogger(__name__)


def classify_intent(content: str) -> str:
    """Classify a buyer message into a coarse customer-service intent."""
    text = content.lower()
    if any(word in text for word in ["便宜", "优惠", "刀", "最低", "少点", "降价"]):
        return "price_negotiation"
    if any(word in text for word in ["发货", "快递", "物流", "单号", "几天到"]):
        return "shipping"
    if any(word in text for word in ["退", "换", "坏", "售后", "质量"]):
        return "after_sales"
    if any(word in text for word in ["还有", "在吗", "库存", "能拍", "有货"]):
        return "availability"
    return "general"


def ingest_buyer_messages(messages: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Persist buyer messages and update conversation summaries."""
    inserted = 0
    updated_conversations = set()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        for message in messages:
            conversation_id = str(message.get("conversation_id") or "").strip()
            content = str(message.get("content") or "").strip()
            if not conversation_id or not content:
                continue

            buyer_name = message.get("buyer_name") or "买家"
            received_at = message.get("received_at") or datetime.now().isoformat()
            intent = classify_intent(content)
            message_id = message.get("message_id") or _message_id(conversation_id, content, received_at)
            raw_json = json.dumps(message, ensure_ascii=False)

            cursor.execute("""
                INSERT INTO xianyu_conversations
                (conversation_id, buyer_name, platform, status, last_intent, last_message_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    buyer_name = excluded.buyer_name,
                    last_intent = excluded.last_intent,
                    last_message_at = excluded.last_message_at,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                conversation_id,
                buyer_name,
                message.get("source") or "goofish",
                "open",
                intent,
                received_at,
            ))
            updated_conversations.add(conversation_id)

            cursor.execute("""
                INSERT OR IGNORE INTO xianyu_messages
                (message_id, conversation_id, direction, content, intent, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (message_id, conversation_id, "buyer", content, intent, raw_json, received_at))
            if cursor.rowcount > 0:
                inserted += 1

            # 自动唤醒逻辑：resolved 会话收到新买家消息，转回 open
            # 注意：只唤醒 resolved，pending_handoff/human_taking 不被唤醒
            cursor.execute("""
                UPDATE xianyu_conversations
                SET status = 'open', updated_at = CURRENT_TIMESTAMP
                WHERE conversation_id = ?
                  AND status = 'resolved'
            """, (conversation_id,))

            if cursor.rowcount > 0:
                logger.info(
                    "Auto-wakeup: conversation %s status changed from resolved to open (new buyer message received)",
                    conversation_id
                )

        conn.commit()
        return {
            "inserted": inserted,
            "updated_conversations": len(updated_conversations),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sync_messages_from_platform(platform, limit: int = 20) -> Dict[str, Any]:
    """Read platform messages, persist them, and return local conversations."""
    messages = platform.read_messages(limit=limit)
    ingest_result = ingest_buyer_messages(messages)
    return {
        "status": "success",
        "ingest": ingest_result,
        "messages": messages,
        "conversations": list_conversations(limit=limit),
    }


def persist_competitor_observations(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    inserted = 0
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        for item in results:
            keyword = str(item.get("keyword") or "").strip()
            if not keyword:
                continue
            cursor.execute(
                """
                INSERT INTO competitor_observations
                (keyword, title, price, sold_count, platform, source_label, raw_json, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    keyword,
                    item.get("title"),
                    _to_float(item.get("price")),
                    None if item.get("sold_count") is None else str(item.get("sold_count")),
                    item.get("platform") or item.get("source") or "unknown",
                    item.get("source_label") or item.get("source") or "unknown",
                    json.dumps(item, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
            inserted += 1
        conn.commit()
        return {"inserted": inserted}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def persist_listing_snapshots(listings: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Persist read-only listing snapshots from the real seller page."""
    upserted = 0
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        for item in listings:
            item_id = str(item.get("item_id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not item_id or not title:
                continue
            cursor.execute(
                """
                INSERT INTO xianyu_listing_snapshots
                (item_id, title, status, price, item_url, view_count, want_count,
                 published_at, source_label, raw_json, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    title,
                    item.get("status") or "on_sale",
                    _to_float(item.get("price")),
                    item.get("item_url"),
                    None if item.get("view_count") is None else str(item.get("view_count")),
                    None if item.get("want_count") is None else str(item.get("want_count")),
                    item.get("published_at"),
                    item.get("source_label") or item.get("platform") or "goofish",
                    json.dumps(item, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
            upserted += 1
        conn.commit()
        return {"upserted": upserted}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_listings(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.item_id, s.title, s.status, s.price, s.view_count, s.want_count,
                   s.published_at, s.item_url, s.last_seen_at, s.source_label
            FROM xianyu_listing_snapshots s
            JOIN (
                SELECT item_id, MAX(id) AS max_id
                FROM xianyu_listing_snapshots
                GROUP BY item_id
            ) latest ON latest.max_id = s.id
            ORDER BY s.last_seen_at DESC, s.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "item_id": row[0],
                "title": row[1],
                "status": row[2],
                "price": row[3],
                "view_count": row[4],
                "want_count": row[5],
                "published_at": row[6],
                "item_url": row[7],
                "last_seen_at": row[8],
                "data_source": row[9] or "unknown",
            }
            for row in cursor.fetchall()
        ]
    finally:
        conn.close()


def count_on_sale_listings() -> int:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT s.item_id
                FROM xianyu_listing_snapshots s
                JOIN (
                    SELECT item_id, MAX(id) AS max_id
                    FROM xianyu_listing_snapshots
                    GROUP BY item_id
                ) latest ON latest.max_id = s.id
                WHERE s.status = 'on_sale'
            )
            """
        )
        return cursor.fetchone()[0] or 0
    finally:
        conn.close()


def list_conversations(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT conversation_id, buyer_name, platform, status, last_intent, last_message_at, updated_at
            FROM xianyu_conversations
            ORDER BY updated_at DESC
            LIMIT ?
        """, (limit,))
        conversations = []
        for row in cursor.fetchall():
            conversations.append({
                "conversation_id": row[0],
                "buyer_name": row[1],
                "platform": row[2],
                "status": row[3],
                "last_intent": row[4],
                "last_message_at": row[5],
                "updated_at": row[6],
            })
        return conversations
    finally:
        conn.close()


def list_messages(conversation_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT message_id, conversation_id, direction, content, intent, draft_reply,
                   approval_id, sent_status, created_at
            FROM xianyu_messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, id ASC
        """, (conversation_id,))
        return [
            {
                "message_id": row[0],
                "conversation_id": row[1],
                "direction": row[2],
                "content": row[3],
                "intent": row[4],
                "draft_reply": row[5],
                "approval_id": row[6],
                "sent_status": row[7],
                "created_at": row[8],
            }
            for row in cursor.fetchall()
        ]
    finally:
        conn.close()


def save_draft(conversation_id: str, draft: str) -> None:
    latest = _latest_buyer_message(conversation_id)
    if not latest:
        raise ValueError(f"conversation not found or has no buyer messages: {conversation_id}")

    message_id = latest[0]
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE xianyu_messages SET draft_reply = ? WHERE message_id = ?", (draft, message_id))
        conn.commit()
    finally:
        conn.close()


def save_generated_artifact(
    *,
    artifact_type: str,
    payload: Dict[str, Any],
    keyword: Optional[str] = None,
    conversation_id: Optional[str] = None,
    source_label: str = "real",
    summary_text: Optional[str] = None,
) -> Dict[str, Any]:
    artifact_id = f"{artifact_type}-{uuid.uuid4().hex[:12]}"
    payload_json = json.dumps(payload, ensure_ascii=False)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO xianyu_generated_artifacts
            (artifact_id, artifact_type, keyword, conversation_id, source_label, summary_text, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                artifact_type,
                keyword,
                conversation_id,
                source_label,
                summary_text,
                payload_json,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        return {
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "keyword": keyword,
            "conversation_id": conversation_id,
            "source_label": source_label,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_generated_artifacts(
    *,
    artifact_type: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        sql = """
            SELECT artifact_id, artifact_type, keyword, conversation_id, source_label,
                   summary_text, payload_json, created_at
            FROM xianyu_generated_artifacts
        """
        params: list[Any] = []
        if artifact_type:
            sql += " WHERE artifact_type = ?"
            params.append(artifact_type)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cursor.execute(sql, params)
        artifacts: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            try:
                payload = json.loads(row[6]) if row[6] else None
            except json.JSONDecodeError:
                payload = None
            artifacts.append(
                {
                    "artifact_id": row[0],
                    "artifact_type": row[1],
                    "keyword": row[2],
                    "conversation_id": row[3],
                    "source_label": row[4],
                    "summary_text": row[5],
                    "payload": payload,
                    "created_at": row[7],
                }
            )
        return artifacts
    finally:
        conn.close()


def latest_generated_artifact(artifact_type: str) -> Optional[Dict[str, Any]]:
    artifacts = list_generated_artifacts(artifact_type=artifact_type, limit=1)
    return artifacts[0] if artifacts else None


def count_generated_artifacts() -> int:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM xianyu_generated_artifacts")
        return cursor.fetchone()[0] or 0
    finally:
        conn.close()


def attach_approval(conversation_id: str, approval_id: str) -> None:
    latest = _latest_buyer_message(conversation_id)
    if not latest:
        raise ValueError(f"conversation not found or has no buyer messages: {conversation_id}")

    message_id = latest[0]
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE xianyu_messages SET approval_id = ?, sent_status = 'approval_pending' WHERE message_id = ?",
            (approval_id, message_id),
        )
        conn.commit()
    finally:
        conn.close()


def _latest_buyer_message(conversation_id: str) -> Optional[tuple]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT message_id, content, intent
            FROM xianyu_messages
            WHERE conversation_id = ? AND direction = 'buyer'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """, (conversation_id,))
        return cursor.fetchone()
    finally:
        conn.close()


def _message_id(conversation_id: str, content: str, received_at: str) -> str:
    payload = f"{conversation_id}\n{received_at}\n{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        cleaned = str(value).replace("¥", "").replace(",", "").strip()
        return float(cleaned)
    except (TypeError, ValueError):
        return None


# ==================== 审批闭环 (②子项1) ====================

def is_approval_approved(approval_id: str, expected_workflow_type: str, *, order_id: Optional[str] = None) -> bool:
    """审批通过校验: status='approved' + 动作类型匹配 + (ship)订单号匹配，
    防 approved 单跨动作(list↔ship)/跨对象(订单A↔B)挪用。"""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT status, workflow_type, order_id FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return False
        status, wf_type, row_order_id = row[0], row[1], row[2]
        return (
            status == "approved"
            and wf_type == expected_workflow_type
            and (order_id is None or row_order_id == order_id)
        )
    finally:
        conn.close()


def set_approval_status(approval_id: str, status: str, *, expect_current: Optional[str] = None) -> bool:
    """更新审批单状态; expect_current 非空时只在当前状态匹配才更新(防重复批准/复活已拒单)。"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        if expect_current is not None:
            cursor.execute(
                "UPDATE approvals SET status = ? WHERE approval_id = ? AND status = ?",
                (status, approval_id, expect_current),
            )
        else:
            cursor.execute(
                "UPDATE approvals SET status = ? WHERE approval_id = ?",
                (status, approval_id),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
