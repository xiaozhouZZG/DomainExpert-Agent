"""
自动回复业务层 — 纯逻辑，不碰浏览器

decide_reply(buyer_msg, conversation_id) -> {action, text, score, reason}
  - handoff: 碰钱/敏感意图 或 检索 gray/not_found → 转人工
  - reply: 检索 high → LLM 基于检索资料生成回复文本

纯函数，可单独跑，不依赖浏览器、不调 send_reply、不写 DB。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

SENSITIVE_PATTERNS = [
    r"讲价", r"便宜点", r"少点", r"下单", r"拍了", r"付款",
    r"退款", r"退钱", r"纠纷", r"投诉", r"催发货", r"发货了吗",
    r"包邮", r"减点", r"优惠", r"打折", r"抹零", r"零头",
    r"货到付款", r"分期", r"花呗", r"先发货", r"验货",
]
_SENSITIVE_RE = re.compile("|".join(SENSITIVE_PATTERNS))


def is_sensitive(text: str) -> bool:
    """是否命中碰钱/敏感意图"""
    return bool(_SENSITIVE_RE.search(text))


def decide_reply(buyer_msg: str, conversation_id: str = "") -> dict[str, Any]:
    """
    纯业务决策：给一条买家消息，决定回复动作

    Args:
        buyer_msg: 买家消息文本
        conversation_id: 会话 ID（日志用）

    Returns:
        {
            "action": "handoff" | "reply",
            "text": str,          # action=reply 时的回复文本；action=handoff 时为空
            "score": float,       # 检索置信度
            "reason": str,        # 决策原因（日志/展示用）
        }
    """
    # 1. 碰钱/敏感意图 → handoff
    if is_sensitive(buyer_msg):
        logger.info(f"[决策] 敏感意图: '{buyer_msg}' → handoff")
        return {
            "action": "handoff",
            "text": "",
            "score": 0.0,
            "reason": "敏感意图拦截",
        }

    # 2. 检索三段式
    from knowledge.retrieval_gateway import search_with_confidence

    retrieval = search_with_confidence(query=buyer_msg, top_k=5)
    status = retrieval.get("status", "not_found")
    score = retrieval.get("confidence_score", 0.0)
    results = retrieval.get("results", [])

    logger.info(f"[决策] 检索: status={status} score={score:.4f} hits={len(results)}")

    if status != "high":
        reason = f"检索{status}(score={score:.4f})"
        logger.info(f"[决策] {reason} → handoff")
        return {
            "action": "handoff",
            "text": "",
            "score": round(score, 4),
            "reason": reason,
        }

    # 3. high → LLM 生成回复
    reply_text = _generate_reply(buyer_msg, results)

    if not reply_text:
        logger.warning("[决策] LLM 生成失败 → handoff")
        return {
            "action": "handoff",
            "text": "",
            "score": round(score, 4),
            "reason": "LLM生成失败",
        }

    logger.info(f"[决策] high(score={score:.4f}) → reply({len(reply_text)}字)")
    return {
        "action": "reply",
        "text": reply_text,
        "score": round(score, 4),
        "reason": f"检索high(score={score:.4f})",
    }


def _generate_reply(query: str, retrieval_results: list[dict]) -> str:
    """基于检索资料让 LLM 生成回复（同步调用）"""
    from core.config_manager import ConfigManager

    config = ConfigManager.get_llm_config()
    base_url = config.get("base_url", "")
    api_key = config.get("api_key", "")
    model = config.get("model", "")

    if not base_url or not api_key:
        logger.error("_generate_reply: LLM 配置缺失")
        return ""

    context_parts = []
    for i, r in enumerate(retrieval_results[:5]):
        s = r.get("score", 0)
        t = r.get("content") or r.get("text", "")
        context_parts.append(f"[资料{i+1}](相关度:{s:.2f})\n{t}")
    context_text = "\n\n".join(context_parts)

    system_prompt = (
        "你是闲鱼卖家的客服助手。请根据以下检索到的资料回答买家问题。\n"
        "【严格约束】\n"
        "1. 只能使用检索到的资料中的信息，不准编造\n"
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

        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.post(url, json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "stream": False,
            }, headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            })
            resp.raise_for_status()
            reply = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            if reply:
                logger.info(f"_generate_reply: 成功({len(reply)}字)")
            return reply
    except Exception as e:
        logger.error(f"_generate_reply: 失败: {e}")
        return ""
