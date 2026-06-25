"""
一次性真测脚本：登录 + 自动回复，同一个进程内完成

用法: python scripts/test_auto_reply.py
"""
import json
import logging
import sys
import time

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/test_auto_reply.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger("test_auto_reply")


def main():
    logger.info("=" * 60)
    logger.info("真测开始：登录 → 小号发消息 → 自动回复 → 硬校验")
    logger.info("=" * 60)

    # Step 1: 登录
    from platforms.goofish_playwright import GoofishPlaywrightPlatform

    platform = GoofishPlaywrightPlatform()

    logger.info("Step 1: 检查登录状态...")
    # 先尝试 poll 看是否已登录（不走 login_interactive，避免重新扫码）
    poll_result = platform.poll_unread_conversations()
    poll_status = poll_result.get("status", "failed")

    if poll_status == "need_login":
        logger.info("未登录，需要扫码...")
        login_result = platform.login_interactive(max_wait_seconds=300)
        logger.info(f"登录结果: {json.dumps(login_result, ensure_ascii=False)}")
    elif poll_status == "ready":
        logger.info("已登录！直接开始测试")
    else:
        logger.warning(f"poll 返回: status={poll_status} detail={poll_result.get('detail','')}")
        # 如果不是 need_login，可能是页面加载问题，尝试登录
        if poll_status == "failed":
            logger.info("尝试登录...")
            login_result = platform.login_interactive(max_wait_seconds=300)
            logger.info(f"登录结果: {json.dumps(login_result, ensure_ascii=False)}")

    # Step 2: 运行自动回复
    logger.info("Step 2: 运行自动回复...")
    from core.auto_reply_orchestrator import run_once, _conversation_id_for

    result = run_once()

    logger.info("=" * 60)
    logger.info("真测结果:")
    logger.info(json.dumps(result, ensure_ascii=False, indent=2))
    logger.info("=" * 60)

    # Step 3: 检查 DB 记录
    logger.info("Step 3: 检查 DB 记录...")
    from database.connection import get_db_connection

    conversation_id = _conversation_id_for("海王星上蹿下跳的豆浆")
    conn = get_db_connection()
    cursor = conn.cursor()

    # 会话状态
    cursor.execute(
        "SELECT status, last_intent, updated_at FROM xianyu_conversations WHERE conversation_id = ?",
        (conversation_id,),
    )
    conv_row = cursor.fetchone()
    if conv_row:
        logger.info(f"会话状态: status={conv_row[0]} last_intent={conv_row[1]} updated_at={conv_row[2]}")

    # 最新消息
    cursor.execute(
        "SELECT message_id, direction, content, draft_reply, sent_status, created_at FROM xianyu_messages WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT 5",
        (conversation_id,),
    )
    msg_rows = cursor.fetchall()
    for row in msg_rows:
        draft = None
        if row[3]:
            try:
                draft = json.loads(row[3])
            except:
                draft = row[3][:100]
        logger.info(f"  消息: id={row[0][:8]} dir={row[1]} content={row[2][:50]} status={row[4]} draft={draft}")

    conn.close()
    logger.info("真测完成！")


if __name__ == "__main__":
    import os
    os.makedirs("logs", exist_ok=True)
    main()
