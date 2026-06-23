"""
影子模式自动回复编排器

读到买家未读 → 检索 → 护栏决策 → 生成草稿存 DB → 绝不真发

架构：一趟扫描就地处理
  poll_unread_conversations 点进会话清未读是不可逆的，
  所以所有处理必须在第一次点开那一趟内完成。
  实现方式：把处理逻辑作为回调 on_unread_message 传入 poll，
  poll 在读到买家消息后立刻调用回调，绝不再扫第二次。

铁律：
1. 第一版只走影子模式：只生成草稿写进 DB，绝对不调用 send_reply
2. 测试会话白名单：只处理指定买家，其他会话跳过
3. 复用 poll_unread_conversations() 拿未读（通过回调就地处理）
4. 走 browser_worker 线程，sync Playwright 规矩
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

from database.connection import get_db_connection
from knowledge.retrieval_gateway import search_with_confidence

logger = logging.getLogger(__name__)

# 测试会话白名单 — 只有这些买家的会话会被处理
SHADOW_WHITELIST = [
    "海王星上蹿下跳的豆浆",
]

# 碰钱/敏感意图关键词
SENSITIVE_INTENT_PATTERNS = [
    r"讲价", r"便宜点", r"少点", r"下单", r"拍了", r"付款",
    r"退款", r"退钱", r"纠纷", r"投诉", r"催发货", r"发货了吗",
    r"包邮", r"减点", r"优惠", r"打折", r"抹零", r"零头",
    r"货到付款", r"分期", r"花呗", r"先发货", r"验货",
]
SENSITIVE_INTENT_RE = re.compile("|".join(SENSITIVE_INTENT_PATTERNS))


def _conversation_id_for(buyer_name: str) -> str:
    """生成闲鱼会话的临时 conversation_id（用 buyer_name 当 key）"""
    return f"goofish:{buyer_name}"


def _message_id_for(conversation_id: str, content: str, timestamp: str) -> str:
    """生成消息唯一 ID（复用 xianyu_service 的 sha256 方案）"""
    payload = f"{conversation_id}\n{timestamp}\n{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _ensure_conversation(conversation_id: str, buyer_name: str) -> None:
    """确保会话记录存在（不存在则创建）"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO xianyu_conversations (conversation_id, buyer_name, platform, status, last_message_at, created_at, updated_at)
            VALUES (?, ?, 'goofish', 'open', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(conversation_id) DO UPDATE SET
                buyer_name = excluded.buyer_name,
                last_message_at = excluded.last_message_at,
                updated_at = CURRENT_TIMESTAMP
        """, (conversation_id, buyer_name, datetime.now().isoformat()))
        conn.commit()
    finally:
        conn.close()


def _is_message_processed(message_id: str) -> bool:
    """检查消息是否已处理过（已生成草稿或已转人工）"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT draft_reply, sent_status FROM xianyu_messages
            WHERE message_id = ?
        """, (message_id,))
        row = cursor.fetchone()
        if row is None:
            return False
        # draft_reply 非空 = 已处理（不管内容是草稿还是 handoff 元数据）
        draft_reply = row[0]
        return draft_reply is not None and draft_reply != ""
    finally:
        conn.close()


def _save_buyer_message(
    message_id: str,
    conversation_id: str,
    content: str,
    intent: str | None = None,
) -> None:
    """保存买家消息到 xianyu_messages"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO xianyu_messages (message_id, conversation_id, direction, content, intent, sent_status, created_at)
            VALUES (?, ?, 'buyer', ?, ?, 'received', CURRENT_TIMESTAMP)
            ON CONFLICT(message_id) DO UPDATE SET
                content = excluded.content,
                intent = excluded.intent
        """, (message_id, conversation_id, content, intent))
        conn.commit()
    finally:
        conn.close()


def _save_draft_reply(message_id: str, conversation_id: str, draft_data: dict) -> None:
    """保存草稿/handoff 到 draft_reply 字段"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        draft_json = json.dumps(draft_data, ensure_ascii=False)
        cursor.execute("""
            UPDATE xianyu_messages
            SET draft_reply = ?
            WHERE message_id = ?
        """, (draft_json, message_id))
        conn.commit()
    finally:
        conn.close()


def _update_conversation_status(conversation_id: str, status: str, last_intent: str | None = None) -> None:
    """更新会话状态"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE xianyu_conversations
            SET status = ?, last_intent = ?, updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = ?
        """, (status, last_intent, conversation_id))
        conn.commit()
    finally:
        conn.close()


def _is_sensitive_intent(text: str) -> bool:
    """检查是否命中碰钱/敏感意图"""
    return bool(SENSITIVE_INTENT_RE.search(text))


def _generate_draft_with_llm(query: str, retrieval_results: list[dict]) -> str:
    """
    用 LLM 基于检索到的资料生成草稿（同步调用）

    prompt 严格约束：只能用检索到的资料、不准编
    """
    from core.config_manager import ConfigManager

    config = ConfigManager.get_llm_config()
    base_url = config.get("base_url", "")
    api_key = config.get("api_key", "")
    model = config.get("model", "")

    if not base_url or not api_key:
        logger.error("shadow_pipeline: LLM 配置缺失，无法生成草稿")
        return ""

    # 构建上下文
    context_parts = []
    for i, r in enumerate(retrieval_results[:5]):
        score = r.get("score", 0)
        text = r.get("text", "")
        context_parts.append(f"[资料{i+1}] (相关度: {score:.2f})\n{text}")

    context_text = "\n\n".join(context_parts)

    system_prompt = (
        "你是闲鱼卖家的客服助手。请根据以下检索到的资料回答买家问题。\n"
        "【严格约束】\n"
        "1. 只能使用检索到的资料中的信息，不准编造任何内容\n"
        "2. 如果资料不足以完整回答，请明确说明\n"
        "3. 回复要简洁友好，适合闲鱼聊天场景\n"
        "4. 不要提及'检索'、'资料'等技术细节"
    )

    user_prompt = f"检索到的资料：\n{context_text}\n\n买家问题：{query}\n\n请基于以上资料回答："

    # 同步调用 OpenAI 兼容接口
    try:
        if "/chat/completions" in base_url:
            url = base_url
        elif base_url.endswith("/v1"):
            url = f"{base_url}/chat/completions"
        else:
            url = f"{base_url}/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "stream": False,
        }

        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            draft = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if draft:
                logger.info(f"shadow_pipeline: LLM 生成草稿成功（{len(draft)} 字）")
            else:
                logger.warning("shadow_pipeline: LLM 返回空草稿")
            return draft

    except Exception as e:
        logger.error(f"shadow_pipeline: LLM 调用失败: {e}")
        return ""


def run_shadow_pipeline() -> dict[str, Any]:
    """
    影子模式编排器主入口（一趟扫描就地处理）

    架构：poll_unread_conversations 点进会话清未读是不可逆的，
    所以所有处理（白名单过滤、去重、敏感意图拦截、检索、决策、存草稿）
    必须在点进会话、读到买家消息的那一刻就地完成，绝不能"读完出来再扫一次"。

    实现：把处理逻辑作为回调 on_unread_message 传入 poll_unread_conversations，
    poll 在读到买家消息后立刻调用回调，一趟扫描完成所有工作。

    铁律：
    1. 只走影子模式：只生成草稿写进 DB，绝对不调用 send_reply
    2. 测试会话白名单：只处理指定买家，其他会话跳过
    3. 按 message_id 去重，别重复生成
    4. worker 线程、sync Playwright 规矩、不 asyncio.run

    Returns:
        结构化结果，包含每个会话的 decision/score/草稿/转人工原因
    """
    import httpx as _  # 确保可用

    logger.info("=" * 60)
    logger.info("shadow_pipeline: 开始执行影子模式编排器（一趟扫描就地处理）")
    logger.info("shadow_pipeline: 【铁律】绝不调用 send_reply，绝不真发消息")

    pipeline_results = []
    total_scanned = [0]  # 用列表包装以便回调内修改

    def on_unread_message(conv: dict) -> None:
        """
        回调：在 poll 点进会话、读到买家消息的那一刻就地处理。

        这个回调在 browser_worker 线程内执行，此时会话已被点开、
        未读已清，所以必须在此刻完成所有决策和存储。
        """
        total_scanned[0] += 1

        buyer_name = conv.get("buyer_name", "")
        last_buyer_msg = conv.get("last_buyer_msg", "")
        unread_count = conv.get("unread_count", 0)

        # ===== 白名单过滤 =====
        if buyer_name not in SHADOW_WHITELIST:
            logger.info(f"shadow_pipeline: 跳过非白名单会话: '{buyer_name}'")
            return

        logger.info(f"shadow_pipeline: 处理白名单会话: '{buyer_name}' (未读: {unread_count}, 消息: '{last_buyer_msg}')")

        # ===== 关联真实会话 =====
        conversation_id = _conversation_id_for(buyer_name)
        logger.info(f"shadow_pipeline: 会话 ID: '{conversation_id}'")

        _ensure_conversation(conversation_id, buyer_name)

        # 保存买家消息
        now_iso = datetime.now().isoformat()
        msg_id = _message_id_for(conversation_id, last_buyer_msg, now_iso)
        _save_buyer_message(msg_id, conversation_id, last_buyer_msg)

        # ===== 去重 =====
        if _is_message_processed(msg_id):
            logger.info(f"shadow_pipeline: 消息已处理过 (msg_id={msg_id[:8]}...)，跳过")
            pipeline_results.append({
                "buyer_name": buyer_name,
                "conversation_id": conversation_id,
                "decision": "skipped",
                "reason": "消息已处理过，去重生效",
                "message_id": msg_id[:8],
            })
            return

        # ===== 碰钱/敏感意图前置拦截 =====
        if _is_sensitive_intent(last_buyer_msg):
            logger.info(f"shadow_pipeline: 命中敏感意图拦截: '{last_buyer_msg}' → 转人工")

            handoff_data = {
                "type": "handoff",
                "reason": "sensitive_intent",
                "buyer_message": last_buyer_msg,
                "confidence_score": None,
                "suggested_reply": "您好，这个问题我帮您确认一下，稍后回复您",
                "handoff_at": datetime.now().isoformat(),
            }
            _save_draft_reply(msg_id, conversation_id, handoff_data)
            _update_conversation_status(conversation_id, "pending_handoff", "sensitive_intent")

            pipeline_results.append({
                "buyer_name": buyer_name,
                "conversation_id": conversation_id,
                "decision": "handoff",
                "reason": "敏感意图拦截",
                "score": None,
                "message_id": msg_id[:8],
            })
            return

        # ===== 检索三段式 =====
        logger.info(f"shadow_pipeline: 开始检索: query='{last_buyer_msg}'")
        retrieval = search_with_confidence(query=last_buyer_msg, top_k=5)

        retrieval_status = retrieval.get("status", "not_found")
        confidence_score = retrieval.get("confidence_score", 0.0)
        retrieval_results = retrieval.get("results", [])

        logger.info(f"shadow_pipeline: 检索结果: status={retrieval_status}, score={confidence_score:.4f}, hits={len(retrieval_results)}")

        # ===== 决策 =====
        if retrieval_status == "high":
            # 高置信度 → 生成草稿
            logger.info(f"shadow_pipeline: 检索高置信度 ({confidence_score:.4f}) → 生成草稿")

            draft_text = _generate_draft_with_llm(last_buyer_msg, retrieval_results)

            if draft_text:
                draft_data = {
                    "type": "draft",
                    "content": draft_text,
                    "confidence_score": confidence_score,
                    "retrieval_status": retrieval_status,
                    "retrieval_hits": len(retrieval_results),
                    "generated_at": datetime.now().isoformat(),
                }
                _save_draft_reply(msg_id, conversation_id, draft_data)
                _update_conversation_status(conversation_id, "bot", "auto_reply")

                pipeline_results.append({
                    "buyer_name": buyer_name,
                    "conversation_id": conversation_id,
                    "decision": "draft",
                    "score": round(confidence_score, 4),
                    "draft": draft_text,
                    "message_id": msg_id[:8],
                })
            else:
                # LLM 生成失败 → 转人工
                logger.warning("shadow_pipeline: LLM 生成草稿失败 → 转人工")
                handoff_data = {
                    "type": "handoff",
                    "reason": "llm_generation_failed",
                    "confidence_score": confidence_score,
                    "buyer_message": last_buyer_msg,
                    "suggested_reply": "您好，这个问题我帮您确认一下，稍后回复您",
                    "handoff_at": datetime.now().isoformat(),
                }
                _save_draft_reply(msg_id, conversation_id, handoff_data)
                _update_conversation_status(conversation_id, "pending_handoff", "llm_failed")

                pipeline_results.append({
                    "buyer_name": buyer_name,
                    "conversation_id": conversation_id,
                    "decision": "handoff",
                    "reason": "LLM 生成失败",
                    "score": round(confidence_score, 4),
                    "message_id": msg_id[:8],
                })

        elif retrieval_status == "gray":
            # 灰区 → 转人工
            logger.info(f"shadow_pipeline: 检索灰区 ({confidence_score:.4f}) → 转人工")

            handoff_data = {
                "type": "handoff",
                "reason": "retrieval_gray",
                "confidence_score": confidence_score,
                "buyer_message": last_buyer_msg,
                "suggested_reply": "您好，这个问题我帮您确认一下，稍后回复您",
                "handoff_at": datetime.now().isoformat(),
            }
            _save_draft_reply(msg_id, conversation_id, handoff_data)
            _update_conversation_status(conversation_id, "pending_handoff", "retrieval_gray")

            pipeline_results.append({
                "buyer_name": buyer_name,
                "conversation_id": conversation_id,
                "decision": "handoff",
                "reason": "检索灰区",
                "score": round(confidence_score, 4),
                "message_id": msg_id[:8],
            })

        else:
            # not_found → 转人工
            logger.info(f"shadow_pipeline: 检索不到 ({confidence_score:.4f}) → 转人工")

            handoff_data = {
                "type": "handoff",
                "reason": "retrieval_not_found",
                "confidence_score": confidence_score,
                "buyer_message": last_buyer_msg,
                "suggested_reply": "您好，这个问题我帮您确认一下，稍后回复您",
                "handoff_at": datetime.now().isoformat(),
            }
            _save_draft_reply(msg_id, conversation_id, handoff_data)
            _update_conversation_status(conversation_id, "pending_handoff", "retrieval_not_found")

            pipeline_results.append({
                "buyer_name": buyer_name,
                "conversation_id": conversation_id,
                "decision": "handoff",
                "reason": "检索不到",
                "score": round(confidence_score, 4),
                "message_id": msg_id[:8],
            })

    # ===== 一趟扫描：poll + 回调就地处理 =====
    try:
        from platforms.goofish_playwright import GoofishPlaywrightPlatform
        platform = GoofishPlaywrightPlatform()
        poll_result = platform.poll_unread_conversations(on_unread_message=on_unread_message)
    except Exception as e:
        logger.error(f"shadow_pipeline: poll 调用失败: {e}")
        return {"status": "failed", "detail": f"poll failed: {e}", "results": []}

    poll_status = poll_result.get("status", "failed")

    if poll_status != "ready":
        logger.warning(f"shadow_pipeline: poll 返回非 ready 状态: {poll_status}")
        return {
            "status": poll_status,
            "detail": poll_result.get("detail", ""),
            "results": [],
            "total_scanned": 0,
        }

    # ===== 最终确认：没有调用 send_reply =====
    logger.info("shadow_pipeline: ✓ 全程未调用 send_reply，未发送任何消息")
    logger.info(f"shadow_pipeline: 处理完成，{len(pipeline_results)} 个会话有结果（一趟扫描就地处理）")
    logger.info("=" * 60)

    return {
        "status": "ready",
        "detail": f"Processed {len(pipeline_results)} conversations in single pass",
        "results": pipeline_results,
        "total_scanned": total_scanned[0],
        "send_reply_called": False,  # 铁律：永远 false
    }
