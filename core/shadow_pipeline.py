"""
自动回复编排器 — 一条主线，一趟走完

handle_buyer_message(conv, msg):
  白名单 → 去重 → 入库(统一) → 敏感意图拦截 → 检索三段式 → 生成 → 真发 → 硬校验 → 入库

铁律：
1. 只对测试白名单（海王星）真发，真实买家靠白名单挡死
2. approval 绕过只准测试号用
3. worker 线程、sync Playwright 规矩
4. 发送后硬校验（自己气泡含发送内容），没通过算失败
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

from database.connection import get_db_connection

logger = logging.getLogger(__name__)

# 测试会话白名单 — 只有这些买家的会话会被处理
TEST_WHITELIST = [
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
    """闲鱼会话 ID（用 buyer_name 当 key）"""
    return f"goofish:{buyer_name}"


def _message_id_for(conversation_id: str, content: str, timestamp: str) -> str:
    """消息唯一 ID"""
    payload = f"{conversation_id}\n{timestamp}\n{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _is_message_processed(message_id: str) -> bool:
    """检查消息是否已处理过（draft_reply 非空 = 已处理）"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT draft_reply FROM xianyu_messages WHERE message_id = ?",
            (message_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return False
        return row[0] is not None and row[0] != ""
    finally:
        conn.close()


def _save_sent_reply(message_id: str, conversation_id: str, reply_text: str, confidence_score: float) -> None:
    """保存已发回复到 DB（draft_reply + sent_status='sent'）"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        draft_data = {
            "type": "sent",
            "content": reply_text,
            "confidence_score": confidence_score,
            "sent_at": datetime.now().isoformat(),
        }
        cursor.execute(
            "UPDATE xianyu_messages SET draft_reply = ?, sent_status = 'sent' WHERE message_id = ?",
            (json.dumps(draft_data, ensure_ascii=False), message_id),
        )
        conn.commit()
    finally:
        conn.close()


def _generate_reply_with_rag(query: str, retrieval_results: list[dict]) -> str:
    """基于检索资料让 LLM 生成回复（同步调用，带 RAG 护栏）"""
    from core.config_manager import ConfigManager

    config = ConfigManager.get_llm_config()
    base_url = config.get("base_url", "")
    api_key = config.get("api_key", "")
    model = config.get("model", "")

    if not base_url or not api_key:
        logger.error("handle_buyer_message: LLM 配置缺失，无法生成回复")
        return ""

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

    try:
        import httpx

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
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if reply:
                logger.info(f"handle_buyer_message: LLM 生成回复成功（{len(reply)} 字）")
            else:
                logger.warning("handle_buyer_message: LLM 返回空回复")
            return reply
    except Exception as e:
        logger.error(f"handle_buyer_message: LLM 调用失败: {e}")
        return ""


def handle_buyer_message(conv: dict) -> dict[str, Any]:
    """
    自动回复主线 — 一趟走完

    在 poll 点进会话、读到买家消息后立刻调用。
    顺序：白名单 → 去重 → 入库 → 敏感意图 → 检索 → 生成 → 真发 → 硬校验 → 入库

    Args:
        conv: poll 回调传来的会话数据 {
            "buyer_name", "last_buyer_msg", "unread_count",
            "needs_reply", "last_msg_direction"
        }

    Returns:
        处理结果 {"decision": ..., ...}
    """
    buyer_name = conv.get("buyer_name", "")
    last_buyer_msg = conv.get("last_buyer_msg", "")
    unread_count = conv.get("unread_count", 0)

    # ===== a. 白名单 =====
    if buyer_name not in TEST_WHITELIST:
        logger.info(f"[主线] 跳过非白名单: '{buyer_name}'")
        return {"decision": "skipped", "reason": "非白名单"}

    logger.info(f"[主线] ★ 处理白名单会话: '{buyer_name}' 未读={unread_count} 消息='{last_buyer_msg}'")

    conversation_id = _conversation_id_for(buyer_name)

    # ===== b. 去重 =====
    now_iso = datetime.now().isoformat()
    msg_id = _message_id_for(conversation_id, last_buyer_msg, now_iso)

    if _is_message_processed(msg_id):
        logger.info(f"[主线] 消息已处理过 (msg_id={msg_id[:8]}...)，跳过")
        return {"decision": "skipped", "reason": "去重", "message_id": msg_id[:8]}

    # ===== c. 入库买家消息（统一走 xianyu_service）=====
    from core.xianyu_service import ingest_buyer_messages

    ingest_result = ingest_buyer_messages([{
        "conversation_id": conversation_id,
        "buyer_name": buyer_name,
        "content": last_buyer_msg,
        "source": "goofish",
        "message_id": msg_id,
        "received_at": now_iso,
    }])
    logger.info(f"[主线] 入库完成: inserted={ingest_result.get('inserted', 0)}")

    # ===== d. 碰钱/敏感意图拦截 → 转人工 =====
    if SENSITIVE_INTENT_RE.search(last_buyer_msg):
        logger.info(f"[主线] 命中敏感意图: '{last_buyer_msg}' → 转人工，不发")
        from core.conversation_status import mark_conversation_pending_handoff

        mark_conversation_pending_handoff(
            conversation_id=conversation_id,
            reason="sensitive_intent",
            buyer_message=last_buyer_msg,
            confidence_score=0.0,
            suggested_reply="您好，这个问题我帮您确认一下，稍后回复您",
        )
        return {
            "decision": "handoff",
            "reason": "敏感意图拦截",
            "buyer_name": buyer_name,
            "conversation_id": conversation_id,
        }

    # ===== e. 检索三段式 =====
    from knowledge.retrieval_gateway import search_with_confidence

    retrieval = search_with_confidence(query=last_buyer_msg, top_k=5)
    retrieval_status = retrieval.get("status", "not_found")
    confidence_score = retrieval.get("confidence_score", 0.0)
    retrieval_results = retrieval.get("results", [])

    logger.info(
        f"[主线] 检索结果: status={retrieval_status} score={confidence_score:.4f} hits={len(retrieval_results)}"
    )

    if retrieval_status != "high":
        # gray / not_found → 转人工，不发
        from core.conversation_status import mark_conversation_pending_handoff

        reason_map = {"gray": "retrieval_gray", "not_found": "retrieval_not_found"}
        reason = reason_map.get(retrieval_status, "retrieval_not_found")
        logger.info(f"[主线] 检索 {retrieval_status} (score={confidence_score:.4f}) → 转人工，不发")

        mark_conversation_pending_handoff(
            conversation_id=conversation_id,
            reason=reason,
            buyer_message=last_buyer_msg,
            confidence_score=confidence_score,
        )
        return {
            "decision": "handoff",
            "reason": f"检索{retrieval_status}",
            "score": round(confidence_score, 4),
            "buyer_name": buyer_name,
            "conversation_id": conversation_id,
        }

    # ===== high → LLM 生成回复 =====
    logger.info(f"[主线] 检索 high (score={confidence_score:.4f}) → LLM 生成回复")
    reply_text = _generate_reply_with_rag(last_buyer_msg, retrieval_results)

    if not reply_text:
        logger.warning("[主线] LLM 生成失败 → 转人工")
        from core.conversation_status import mark_conversation_pending_handoff

        mark_conversation_pending_handoff(
            conversation_id=conversation_id,
            reason="llm_generation_failed",
            buyer_message=last_buyer_msg,
            confidence_score=confidence_score,
        )
        return {
            "decision": "handoff",
            "reason": "LLM生成失败",
            "score": round(confidence_score, 4),
            "buyer_name": buyer_name,
            "conversation_id": conversation_id,
        }

    # ===== 真发 =====
    logger.info(f"[主线] 回复内容: '{reply_text[:80]}...' → 调 send_reply 真发")
    from platforms.goofish_playwright import GoofishPlaywrightPlatform

    platform = GoofishPlaywrightPlatform()
    send_result = platform.send_reply(
        conversation_id=conversation_id,
        content=reply_text,
        approval_id="test",  # 测试号绕过审批
        target=buyer_name,
    )

    send_status = send_result.get("status", "failed")
    logger.info(f"[主线] send_reply 返回: status={send_status}")

    if send_status == "sent":
        # 硬校验通过
        logger.info(f"[主线] ✅ 发送成功！硬校验通过，小号应已收到回复")
        _save_sent_reply(msg_id, conversation_id, reply_text, confidence_score)

        # 更新会话状态为 bot
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE xianyu_conversations SET status = 'bot', last_intent = 'auto_reply', updated_at = CURRENT_TIMESTAMP WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "decision": "sent",
            "reply": reply_text,
            "score": round(confidence_score, 4),
            "send_status": send_status,
            "buyer_name": buyer_name,
            "conversation_id": conversation_id,
        }
    else:
        # 发送失败或不确定
        logger.error(f"[主线] ❌ 发送失败: status={send_status} detail={send_result.get('detail', '')}")
        from core.conversation_status import mark_conversation_pending_handoff

        mark_conversation_pending_handoff(
            conversation_id=conversation_id,
            reason=f"send_failed:{send_status}",
            buyer_message=last_buyer_msg,
            confidence_score=confidence_score,
            suggested_reply=reply_text,
        )
        return {
            "decision": "send_failed",
            "reason": f"发送状态: {send_status}",
            "reply": reply_text,
            "score": round(confidence_score, 4),
            "send_status": send_status,
            "send_detail": send_result.get("detail", ""),
            "buyer_name": buyer_name,
            "conversation_id": conversation_id,
        }


def run_auto_reply() -> dict[str, Any]:
    """
    自动回复主入口

    一趟扫描：poll 发现未读 → 回调 handle_buyer_message 就地处理 → 完成
    不二次 poll、不影子绕路、不留测试开关
    """
    logger.info("=" * 60)
    logger.info("[自动回复] 开始一趟扫描")

    results = []
    total_scanned = [0]

    def on_unread_message(conv: dict) -> None:
        """回调：poll 点进会话读到买家消息后，立刻交给主线函数处理"""
        total_scanned[0] += 1
        if conv.get("needs_reply"):
            result = handle_buyer_message(conv)
            results.append(result)

    try:
        from platforms.goofish_playwright import GoofishPlaywrightPlatform

        platform = GoofishPlaywrightPlatform()
        poll_result = platform.poll_unread_conversations(on_unread_message=on_unread_message)
    except Exception as e:
        logger.error(f"[自动回复] poll 失败: {e}")
        return {"status": "failed", "detail": str(e), "results": []}

    poll_status = poll_result.get("status", "failed")
    if poll_status != "ready":
        logger.warning(f"[自动回复] poll 状态: {poll_status}")
        return {
            "status": poll_status,
            "detail": poll_result.get("detail", ""),
            "results": [],
        }

    logger.info(f"[自动回复] 完成: 扫描={total_scanned[0]} 处理={len(results)}")
    logger.info("=" * 60)

    return {
        "status": "ready",
        "results": results,
        "total_scanned": total_scanned[0],
    }
