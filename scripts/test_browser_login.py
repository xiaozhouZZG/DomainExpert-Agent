"""
测试常驻浏览器启动和登录态
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from platforms.goofish_playwright import GoofishPlaywrightPlatform
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_browser_and_login():
    """启动浏览器并检查登录态"""
    platform = GoofishPlaywrightPlatform()

    def action(page):
        logger.info("=" * 80)
        logger.info("测试浏览器启动和登录态")
        logger.info("=" * 80)

        # 导航到闲鱼首页
        logger.info("1. 导航到闲鱼首页...")
        page.goto("https://goofish.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        current_url = page.url
        logger.info(f"   当前URL: {current_url}")

        # 检查登录态（复用现有逻辑）
        logger.info("2. 检查登录态...")

        # 尝试多种方式判断登录态
        looks_logged_out = False

        # 方法1: 检查URL是否包含登录相关
        if "login" in current_url.lower():
            looks_logged_out = True
            logger.info("   ✗ URL包含login，判断为未登录")
        else:
            logger.info("   ✓ URL不包含login")

        # 方法2: 检查页面是否有登录按钮
        try:
            login_button = page.locator('text=/登录|登錄/i').first
            if login_button.is_visible(timeout=2000):
                looks_logged_out = True
                logger.info("   ✗ 检测到登录按钮，判断为未登录")
            else:
                logger.info("   ✓ 未检测到登录按钮")
        except:
            logger.info("   ✓ 未检测到登录按钮")

        # 方法3: 检查是否有用户信息
        try:
            user_info = page.locator('[class*="user"], [class*="avatar"], [class*="nick"]').first
            if user_info.is_visible(timeout=2000):
                logger.info("   ✓ 检测到用户信息元素")
                looks_logged_out = False
            else:
                logger.info("   ⚠️  未检测到用户信息元素")
        except:
            logger.info("   ⚠️  未检测到用户信息元素")

        # 截图
        screenshot_path = Path("logs/browser_login_check.png")
        screenshot_path.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(screenshot_path), full_page=True)
        logger.info(f"   ✓ 截图已保存: {screenshot_path}")

        logger.info("")
        logger.info("=" * 80)
        logger.info(f"_looks_logged_out 判断结果: {looks_logged_out}")
        logger.info("=" * 80)

        if looks_logged_out:
            logger.warning("⚠️  浏览器处于未登录态")
            logger.warning("   请手动登录或使用 /api/xianyu/login 接口")
        else:
            logger.info("✓ 浏览器处于登录态")

        return {
            "status": "ok",
            "looks_logged_out": looks_logged_out,
            "current_url": current_url,
            "screenshot": str(screenshot_path)
        }

    # 使用 browser_manager.with_page 启动浏览器
    result = platform.browser_manager.with_page("test_browser_login", action)

    print("\n")
    print("=" * 80)
    print("结果总结")
    print("=" * 80)
    print(f"当前URL: {result['current_url']}")
    print(f"_looks_logged_out: {result['looks_logged_out']}")
    print(f"截图: {result['screenshot']}")
    print("=" * 80)

    return result


if __name__ == "__main__":
    test_browser_and_login()
