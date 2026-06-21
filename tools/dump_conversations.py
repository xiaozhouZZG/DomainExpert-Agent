"""临时工具：dump闲鱼消息中心的真实DOM结构

用于确定真实URL和selector，不猜测
"""
import sys
from pathlib import Path

# 添加项目根目录到sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import logging
from platforms.goofish_playwright import GoofishPlaywrightPlatform

logger = logging.getLogger(__name__)

def dump_conversations_dom():
    """Dump消息中心的真实DOM结构到logs/"""

    adapter = GoofishPlaywrightPlatform()

    def action(page):
        # 1. 先确保在首页或任意已登录页
        current_url = page.url
        logger.info(f"当前URL: {current_url}")

        # 2. 尝试找到"消息"入口并点击（不猜URL，让浏览器自己跳转）
        logger.info("寻找消息入口...")

        # 可能的消息入口selector（常见的）
        message_selectors = [
            'a[href*="message"]',
            'a[href*="im"]',
            'a:has-text("消息")',
            'text=消息',
            '[class*="message"]',
        ]

        message_url = None
        for selector in message_selectors:
            try:
                element = page.locator(selector).first
                if element.count() > 0:
                    logger.info(f"找到消息入口: {selector}")
                    # 先获取href（如果是链接）
                    try:
                        href = element.get_attribute('href')
                        if href:
                            message_url = href if href.startswith('http') else f"https://goofish.com{href}"
                            logger.info(f"消息入口URL: {message_url}")
                    except:
                        pass

                    # 点击跳转
                    element.click()
                    page.wait_for_load_state('domcontentloaded', timeout=10000)
                    import time
                    time.sleep(2)
                    break
            except Exception as exc:
                logger.debug(f"selector {selector} 未找到: {exc}")
                continue

        # 3. 记录跳转后的真实URL
        final_url = page.url
        logger.info(f"✅ 消息中心真实URL: {final_url}")

        # 4. 检查是否在iframe中
        frames = page.frames
        logger.info(f"页面frame数量: {len(frames)}")

        target_frame = page
        in_iframe = False

        if len(frames) > 1:
            # 可能在iframe中，尝试找到消息列表所在frame
            for idx, frame in enumerate(frames):
                try:
                    frame_url = frame.url
                    logger.info(f"Frame {idx} URL: {frame_url}")

                    # 尝试在这个frame中找消息列表
                    test_html = frame.content()
                    if '会话' in test_html or '消息' in test_html or 'conversation' in test_html:
                        logger.info(f"✅ 消息列表可能在Frame {idx}")
                        target_frame = frame
                        in_iframe = True
                        break
                except:
                    continue

        # 5. dump整个页面HTML
        full_html = target_frame.content()
        dump_dir = Path("logs")
        dump_dir.mkdir(exist_ok=True)

        full_dump_path = dump_dir / "goofish_conversations_full.html"
        with open(full_dump_path, 'w', encoding='utf-8') as f:
            f.write(full_html)
        logger.info(f"✅ 完整HTML已保存: {full_dump_path}")

        # 6. 尝试找会话列表容器并dump
        list_selectors = [
            '[class*="conversation"]',
            '[class*="message-list"]',
            '[class*="im-list"]',
            '[class*="chat-list"]',
            'ul[class*="list"]',
            '[role="list"]',
        ]

        conversations_html = None
        for selector in list_selectors:
            try:
                container = target_frame.locator(selector).first
                if container.count() > 0:
                    conversations_html = container.inner_html()
                    logger.info(f"✅ 找到会话列表容器: {selector}")

                    # 保存会话列表区域HTML
                    list_dump_path = dump_dir / "goofish_conversations_dump.html"
                    with open(list_dump_path, 'w', encoding='utf-8') as f:
                        f.write(f"<!-- 会话列表容器selector: {selector} -->\n")
                        f.write(f"<!-- 是否在iframe: {in_iframe} -->\n")
                        f.write(conversations_html)
                    logger.info(f"✅ 会话列表HTML已保存: {list_dump_path}")
                    break
            except:
                continue

        # 7. 尝试获取前3个会话项的outerHTML
        item_selectors = [
            '[class*="conversation-item"]',
            '[class*="message-item"]',
            '[class*="chat-item"]',
            'li',
        ]

        for selector in item_selectors:
            try:
                if in_iframe:
                    items = target_frame.locator(f'{list_selectors[0]} {selector}')
                else:
                    items = target_frame.locator(selector)

                count = items.count()
                if count > 0:
                    logger.info(f"✅ 找到 {count} 个会话项: {selector}")

                    # 打印前3个的outerHTML
                    for i in range(min(3, count)):
                        item_html = items.nth(i).evaluate('el => el.outerHTML')
                        logger.info(f"\n========== 会话项 {i+1} outerHTML ==========\n{item_html}\n")

                    break
            except Exception as exc:
                logger.debug(f"selector {selector} 获取失败: {exc}")
                continue

        # 8. 截图
        screenshot_path = dump_dir / "goofish_conversations_screenshot.png"
        page.screenshot(path=str(screenshot_path), full_page=True)
        logger.info(f"✅ 截图已保存: {screenshot_path}")

        return {
            "message_url": final_url,
            "in_iframe": in_iframe,
            "conversations_found": conversations_html is not None,
            "dump_files": [
                str(full_dump_path),
                str(dump_dir / "goofish_conversations_dump.html"),
                str(screenshot_path),
            ]
        }

    return adapter.browser_manager.with_page("dump_conversations", action)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = dump_conversations_dom()
    print(f"\n✅ Dump完成: {result}")
