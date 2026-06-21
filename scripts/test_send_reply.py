"""
测试 send_reply 真实发送功能

⚠️ 危险操作：只对测试会话发送 "test"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from platforms.goofish_playwright import GoofishPlaywrightPlatform
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_send_reply():
    """
    测试发送 "test" 到测试会话

    ⚠️ 只对测试会话，不对真实客户
    """
    platform = GoofishPlaywrightPlatform()

    # 步骤1: 手动打开测试会话
    def open_test_conversation(page):
        import time

        logger.info("=" * 80)
        logger.info("步骤1: 打开测试会话")
        logger.info("=" * 80)

        # 导航到 IM 页面
        logger.info("导航到闲鱼 IM...")
        page.goto("https://goofish.com/im", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        logger.info(f"当前URL: {page.url}")

        # 截图 IM 列表
        screenshot_dir = Path("logs/send_reply_test")
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        list_screenshot = screenshot_dir / "01_im_list.png"
        page.screenshot(path=str(list_screenshot))
        logger.info(f"✓ 截图: {list_screenshot}")

        # 提示手动操作
        logger.info("")
        logger.info("=" * 80)
        logger.info("⚠️  请手动操作：")
        logger.info("1. 在浏览器中点击【一个测试会话】（不要点真实客户）")
        logger.info("2. 等待会话页面加载完成")
        logger.info("3. 然后回到终端按 Enter 继续")
        logger.info("=" * 80)

        input("\n按 Enter 继续...")

        time.sleep(2)

        # 截图会话页面
        conversation_screenshot = screenshot_dir / "02_conversation_before_send.png"
        page.screenshot(path=str(conversation_screenshot), full_page=True)
        logger.info(f"✓ 截图（发送前）: {conversation_screenshot}")

        logger.info(f"当前会话URL: {page.url}")

        return {
            "status": "ok",
            "url": page.url,
            "screenshots": [str(list_screenshot), str(conversation_screenshot)]
        }

    # 执行步骤1
    result_open = platform.browser_manager.with_page("open_test_conversation", open_test_conversation)
    logger.info(f"打开会话结果: {result_open}")

    # 步骤2: 发送 "test"
    logger.info("")
    logger.info("=" * 80)
    logger.info("步骤2: 发送测试消息")
    logger.info("=" * 80)

    # 发送消息（使用 approval_id="test" 绕过审批检查）
    test_conversation_id = "test_conversation_001"  # 占位符

    logger.info("")
    logger.info("发送消息: 'test'")

    send_result = platform.send_reply(
        conversation_id=test_conversation_id,
        content="test",
        approval_id="test"  # 测试用，绕过审批
    )

    logger.info("")
    logger.info("=" * 80)
    logger.info("发送结果:")
    logger.info("=" * 80)

    import json
    print(json.dumps(send_result, indent=2, ensure_ascii=False))

    logger.info("")
    logger.info("=" * 80)

    if send_result["status"] == "sent":
        logger.info("✅ 发送成功！")
        logger.info("")
        logger.info("证据:")
        if send_result.get("evidence"):
            for key, value in send_result["evidence"].items():
                logger.info(f"  - {key}: {value}")

        logger.info("")
        logger.info("⚠️  请在闲鱼界面确认是否真的出现了 'test' 消息")

        if "screenshot" in send_result.get("evidence", {}):
            logger.info(f"截图: {send_result['evidence']['screenshot']}")
    else:
        logger.error("✗ 发送失败")
        logger.error(f"原因: {send_result.get('detail', 'Unknown')}")

    logger.info("=" * 80)

    return send_result


if __name__ == "__main__":
    test_send_reply()
