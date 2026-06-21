"""
直接使用已运行的浏览器 dump IM 发送框
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from platforms.goofish_playwright import GoofishPlaywrightPlatform
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def dump_im_sendbox_direct():
    """
    使用 browser_manager.with_page 复用已运行的浏览器
    """
    platform = GoofishPlaywrightPlatform()

    def action(page):
        result = {
            "status": "ok",
            "input_found": False,
            "button_found": False,
        }

        logger.info("=" * 80)
        logger.info("开始 Dump 闲鱼 IM 发送框 DOM")
        logger.info("=" * 80)

        # 1. 导航到 IM 页面
        logger.info("1. 导航到 goofish.com/im...")
        current_url = page.url
        logger.info(f"   当前URL: {current_url}")

        # 如果不在 IM 页面，导航过去
        if "goofish.com/im" not in current_url:
            page.goto("https://goofish.com/im", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            logger.info(f"   ✓ 已导航到: {page.url}")
        else:
            logger.info("   ✓ 已在 IM 页面")

        # 截图目录
        dump_dir = Path("logs/im_sendbox_dump")
        dump_dir.mkdir(parents=True, exist_ok=True)

        # 截图1：当前页面
        screenshot1 = dump_dir / "01_current_page.png"
        page.screenshot(path=str(screenshot1), full_page=True)
        logger.info(f"   ✓ 截图已保存: {screenshot1}")

        # 2. 尝试点击会话（如果在列表页）
        logger.info("2. 检查是否需要打开会话...")
        try:
            # 检查是否已经在会话详情页（有输入框）
            input_check = page.locator('textarea, input[type="text"], div[contenteditable="true"]').first
            if input_check.is_visible(timeout=2000):
                logger.info("   ✓ 已在会话详情页")
            else:
                raise Exception("需要打开会话")
        except:
            # 尝试点击第一个会话
            logger.info("   尝试打开会话...")
            conversation_selectors = [
                'div[class*="conversation"]',
                'div[class*="item"]',
                'li',
            ]

            for selector in conversation_selectors:
                try:
                    elements = page.locator(selector).all()
                    if elements and len(elements) > 0:
                        logger.info(f"   找到 {len(elements)} 个元素 (selector: {selector})")
                        elements[0].click()
                        time.sleep(2)
                        logger.info(f"   ✓ 已点击第一个会话")
                        break
                except:
                    continue

        # 截图2：会话页
        screenshot2 = dump_dir / "02_conversation_page.png"
        page.screenshot(path=str(screenshot2), full_page=True)
        logger.info(f"   ✓ 截图已保存: {screenshot2}")

        # 3. Dump 输入框
        logger.info("3. Dump 输入框 DOM...")
        input_selectors = [
            'textarea',
            'input[type="text"]',
            'div[contenteditable="true"]',
            'div[role="textbox"]',
            '[placeholder*="输入"]',
            '[placeholder*="消息"]',
        ]

        for selector in input_selectors:
            try:
                elem = page.locator(selector).first
                if elem.is_visible(timeout=1000):
                    result["input_found"] = True
                    result["input_selector"] = selector
                    result["input_html"] = elem.evaluate('el => el.outerHTML')

                    logger.info(f"   ✓ 找到输入框 (selector: {selector})")
                    logger.info("")
                    logger.info("=" * 80)
                    logger.info("输入框真实 outerHTML:")
                    logger.info("=" * 80)
                    logger.info(result["input_html"])
                    logger.info("")

                    # 保存
                    input_file = dump_dir / "input_element.html"
                    input_file.write_text(result["input_html"], encoding='utf-8')
                    logger.info(f"✓ 已保存: {input_file}")
                    break
            except:
                continue

        if not result["input_found"]:
            logger.error("   ✗ 未找到输入框")

        # 4. Dump 发送按钮
        logger.info("")
        logger.info("4. Dump 发送按钮 DOM...")
        button_selectors = [
            'button:has-text("发送")',
            'button[type="submit"]',
            'button',
        ]

        for selector in button_selectors:
            try:
                elems = page.locator(selector).all()
                for elem in elems:
                    if elem.is_visible(timeout=500):
                        text = elem.text_content()
                        if "发送" in text or not result["button_found"]:
                            result["button_found"] = True
                            result["button_selector"] = selector
                            result["button_html"] = elem.evaluate('el => el.outerHTML')

                            logger.info(f"   ✓ 找到按钮 (selector: {selector}, text: {text})")
                            logger.info("")
                            logger.info("=" * 80)
                            logger.info("发送按钮真实 outerHTML:")
                            logger.info("=" * 80)
                            logger.info(result["button_html"])
                            logger.info("")

                            # 保存
                            button_file = dump_dir / "send_button.html"
                            button_file.write_text(result["button_html"], encoding='utf-8')
                            logger.info(f"✓ 已保存: {button_file}")
                            break
                if result["button_found"]:
                    break
            except:
                continue

        if not result["button_found"]:
            logger.warning("   ⚠️  未找到发送按钮")

        # 5. 截取输入区特写
        if result["input_found"]:
            logger.info("")
            logger.info("5. 截取输入区特写...")
            try:
                elem = page.locator(result["input_selector"]).first
                screenshot3 = dump_dir / "03_input_area_closeup.png"
                elem.screenshot(path=str(screenshot3))
                logger.info(f"   ✓ 特写已保存: {screenshot3}")
            except Exception as e:
                logger.warning(f"   ⚠️  截取特写失败: {e}")

        logger.info("")
        logger.info("=" * 80)
        logger.info("Dump 完成")
        logger.info("=" * 80)
        logger.info(f"文件保存在: {dump_dir}")

        return result

    # 使用 browser_manager.with_page 复用已运行的浏览器
    result = platform.browser_manager.with_page("dump_im_sendbox", action)

    print("\n\n")
    print("=" * 80)
    print("结果总结")
    print("=" * 80)
    print(f"输入框找到: {result['input_found']}")
    print(f"发送按钮找到: {result['button_found']}")
    if result["input_found"]:
        print(f"输入框选择器: {result['input_selector']}")
    if result["button_found"]:
        print(f"按钮选择器: {result['button_selector']}")
    print("=" * 80)

    return result


if __name__ == "__main__":
    dump_im_sendbox_direct()
