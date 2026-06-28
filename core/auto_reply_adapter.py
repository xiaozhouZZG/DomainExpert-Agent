"""
自动回复接入层 — 薄，只监听+发送+错误分类

get_unread_messages(): poll 返回未读买家会话+最新消息
send_reply(conversation_id, buyer_name, text): 走已验证发送

错误分类（关键，治死循环）：
- 网络/超时 → 退避重试最多 3 次(2s/4s/8s)，仍失败 → 本轮放弃
- need_login → 立刻暂停整个自动循环，绝不重试
- profile_locked → 立刻暂停，绝不重试（浏览器 profile 被其他进程占用）
- 其他未知错 → 记录 + 本轮放弃
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# 测试会话白名单
TEST_WHITELIST = [
    "海王星上蹿下跳的豆浆",
]


def get_unread_messages() -> dict[str, Any]:
    """
    获取未读买家消息

    Returns:
        {
            "status": "ready" | "need_login" | "error",
            "messages": [{"buyer_name", "last_buyer_msg", "unread_count", ...}],
            "detail": str,
        }
    """
    try:
        from platforms.goofish_playwright import GoofishPlaywrightPlatform
        platform = GoofishPlaywrightPlatform()
        result = platform.poll_unread_conversations()

        poll_status = result.get("status", "failed")

        if poll_status == "need_login":
            return {"status": "need_login", "messages": [], "detail": "需要扫码登录"}

        if poll_status != "ready":
            return {"status": "error", "messages": [], "detail": result.get("detail", "")}

        # 只返回买家未读且在白名单的会话
        messages = []
        for conv in result.get("conversations", []):
            if conv.get("needs_reply") and conv.get("buyer_name") in TEST_WHITELIST:
                messages.append(conv)

        return {"status": "ready", "messages": messages, "detail": ""}

    except Exception as e:
        err_str = str(e)
        # profile_locked → 暂停循环，不重试
        if "profile_locked" in err_str:
            logger.error(f"[接入] profile 被占用: {e}")
            return {"status": "profile_locked", "messages": [], "detail": err_str}
        logger.error(f"get_unread_messages 异常: {e}")
        return {"status": "error", "messages": [], "detail": str(e)}


def send_reply(conversation_id: str, buyer_name: str, text: str) -> dict[str, Any]:
    """
    发送回复（带退避重试）

    Args:
        conversation_id: 会话 ID
        buyer_name: 买家名（用于选会话）
        text: 回复内容

    Returns:
        {"status": "sent" | "failed" | "need_login", "detail": str}
    """
    # 退避重试：网络/超时最多 3 次
    backoff = [2, 4, 8]

    for attempt in range(len(backoff) + 1):
        try:
            from platforms.goofish_playwright import GoofishPlaywrightPlatform
            platform = GoofishPlaywrightPlatform()
            result = platform.send_reply(
                conversation_id=conversation_id,
                content=text,
                target=buyer_name,   # ②子项3: 拔 approval_id="test"(send 已解耦, 护栏在白名单+decide_reply)
            )

            send_status = result.get("status", "failed")

            # 硬校验通过
            if send_status == "sent":
                logger.info(f"[接入] send_reply 成功: buyer={buyer_name}")
                return {"status": "sent", "detail": ""}

            # 需要登录
            if send_status == "need_login":
                logger.warning("[接入] send_reply 返回 need_login")
                return {"status": "need_login", "detail": "需要扫码登录"}

            # 发送失败
            if attempt < len(backoff):
                wait = backoff[attempt]
                logger.warning(f"[接入] send_reply 失败(attempt={attempt+1}), {wait}s后重试: {result.get('detail', '')}")
                time.sleep(wait)
                continue

            logger.error(f"[接入] send_reply 最终失败: status={send_status} detail={result.get('detail', '')}")
            return {"status": "failed", "detail": result.get("detail", "")}

        except ConnectionError as e:
            if attempt < len(backoff):
                wait = backoff[attempt]
                logger.warning(f"[接入] 网络错误(attempt={attempt+1}), {wait}s后重试: {e}")
                time.sleep(wait)
                continue
            logger.error(f"[接入] 网络错误最终失败: {e}")
            return {"status": "failed", "detail": f"网络错误: {e}"}

        except TimeoutError as e:
            if attempt < len(backoff):
                wait = backoff[attempt]
                logger.warning(f"[接入] 超时(attempt={attempt+1}), {wait}s后重试: {e}")
                time.sleep(wait)
                continue
            logger.error(f"[接入] 超时最终失败: {e}")
            return {"status": "failed", "detail": f"超时: {e}"}

        except Exception as e:
            # 未知错误 → 不重试，本轮放弃
            err_str = str(e)
            if "need_login" in err_str.lower() or "login" in err_str.lower():
                return {"status": "need_login", "detail": err_str}
            if "profile_locked" in err_str:
                logger.error(f"[接入] profile 被占用: {e}")
                return {"status": "profile_locked", "detail": err_str}
            logger.error(f"[接入] send_reply 未知异常: {e}")
            return {"status": "failed", "detail": str(e)}
