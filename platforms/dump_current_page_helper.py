"""dump当前已打开的页面（不导航）"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def dump_current_page_action(page):
    """dump当前打开的/im页面，不做任何导航"""
    result = {
        "all_pages": [],
        "target_page_url": None,
        "in_iframe": False,
        "dump_files": [],
        "conversation_list_items": [],
        "message_bubbles": [],
        "error": None,
    }

    try:
        dump_dir = Path("logs")
        dump_dir.mkdir(exist_ok=True)

        # 1. 获取context的所有pages
        from platforms.browser_manager import get_goofish_browser_manager
        browser_manager = get_goofish_browser_manager()
        context = browser_manager._context

        if not context:
            result["error"] = "context不存在"
            return result

        all_pages = context.pages
        logger.info(f"context中共有 {len(all_pages)} 个page")

        # 打印所有page的URL
        for i, p in enumerate(all_pages):
            url = p.url
            result["all_pages"].append({"index": i, "url": url})
            logger.info(f"Page {i}: {url}")

        # 2. 优先找/im页面
        target_page = None
        for p in all_pages:
            url = p.url
            if '/im' in url:
                target_page = p
                result["target_page_url"] = url
                logger.info(f"✅ 找到/im页面: {url}")
                break

        # 如果没找到/im，使用当前page
        if not target_page:
            target_page = page
            result["target_page_url"] = page.url
            logger.info(f"使用当前page: {page.url}")

        # 3. dump完整HTML
        full_html = target_page.content()
        html_path = dump_dir / "goofish_im_dump.html"
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(full_html)
        result["dump_files"].append(str(html_path))
        logger.info(f"✅ 完整HTML已保存: {html_path}")

        # 4. 截图
        screenshot_path = dump_dir / "goofish_im.png"
        target_page.screenshot(path=str(screenshot_path), full_page=True)
        result["dump_files"].append(str(screenshot_path))
        logger.info(f"✅ 截图已保存: {screenshot_path}")

        # 5. 检查iframe
        frames = target_page.frames
        logger.info(f"页面共有 {len(frames)} 个frame")

        target_frame = target_page
        if len(frames) > 1:
            result["in_iframe"] = True
            logger.info("页面包含iframe，尝试定位消息内容所在frame...")

            for idx, frame in enumerate(frames):
                try:
                    frame_url = frame.url
                    logger.info(f"Frame {idx} URL: {frame_url}")

                    # 测试frame内容
                    test_html = frame.content()
                    if '会话' in test_html or '消息' in test_html or 'conversation' in test_html or 'message' in test_html or 'im' in test_html.lower():
                        logger.info(f"✅ 消息内容可能在Frame {idx}")
                        target_frame = frame

                        # dump iframe HTML
                        iframe_html_path = dump_dir / "goofish_im_iframe.html"
                        with open(iframe_html_path, 'w', encoding='utf-8') as f:
                            f.write(f"<!-- iframe {idx} URL: {frame_url} -->\n")
                            f.write(test_html)
                        result["dump_files"].append(str(iframe_html_path))
                        logger.info(f"✅ iframe HTML已保存: {iframe_html_path}")
                        break
                except Exception as exc:
                    logger.debug(f"Frame {idx} 检查失败: {exc}")

        # 6. 查找左侧"会话列表"的会话项（前3个）
        logger.info("=== 开始查找左侧会话列表项 ===")
        conversation_selectors = [
            '[class*="conversation"]',
            '[class*="session"]',
            '[class*="contact"]',
            '[class*="chat-item"]',
            '[class*="dialog"]',
            '[class*="list"] > li',
            '[class*="item"]',
        ]

        for selector in conversation_selectors:
            try:
                items = target_frame.locator(selector).all()
                count = len(items)
                if 0 < count < 100:
                    logger.info(f"找到 {count} 个会话列表项候选: {selector}")

                    for i in range(min(3, count)):
                        try:
                            outer_html = items[i].evaluate('el => el.outerHTML')
                            result["conversation_list_items"].append({
                                "index": i,
                                "selector": selector,
                                "html_preview": outer_html[:800] if len(outer_html) > 800 else outer_html
                            })
                            logger.info(f"\n===== 左侧会话项 {i+1} ({selector}) =====\n{outer_html[:500]}...\n")

                            if i == 0:
                                # 保存第一个会话项完整HTML
                                first_conv_path = dump_dir / "goofish_im_conversation_item_0.html"
                                with open(first_conv_path, 'w', encoding='utf-8') as f:
                                    f.write(f"<!-- 左侧会话列表项 selector: {selector} -->\n{outer_html}")
                                result["dump_files"].append(str(first_conv_path))
                        except Exception as inner_exc:
                            logger.debug(f"获取会话项{i}失败: {inner_exc}")

                    if len(result["conversation_list_items"]) > 0:
                        break
            except Exception as exc:
                logger.debug(f"selector '{selector}' 失败: {exc}")

        # 7. 查找右侧"对话区"的消息气泡（前5条）
        logger.info("=== 开始查找右侧消息气泡 ===")
        bubble_selectors = [
            '[class*="message-bubble"]',
            '[class*="chat-bubble"]',
            '[class*="msg"]',
            '[class*="bubble"]',
            '[class*="message-item"]',
            '[class*="chat-content"]',
            '[class*="dialog-item"]',
        ]

        for selector in bubble_selectors:
            try:
                bubbles = target_frame.locator(selector).all()
                count = len(bubbles)
                if 0 < count < 200:
                    logger.info(f"找到 {count} 个消息气泡候选: {selector}")

                    for i in range(min(5, count)):
                        try:
                            outer_html = bubbles[i].evaluate('el => el.outerHTML')
                            result["message_bubbles"].append({
                                "index": i,
                                "selector": selector,
                                "html_preview": outer_html[:600] if len(outer_html) > 600 else outer_html
                            })
                            logger.info(f"\n===== 右侧消息气泡 {i+1} ({selector}) =====\n{outer_html[:400]}...\n")

                            if i == 0:
                                # 保存第一个气泡完整HTML
                                first_bubble_path = dump_dir / "goofish_im_message_bubble_0.html"
                                with open(first_bubble_path, 'w', encoding='utf-8') as f:
                                    f.write(f"<!-- 右侧消息气泡 selector: {selector} -->\n{outer_html}")
                                result["dump_files"].append(str(first_bubble_path))
                        except Exception as inner_exc:
                            logger.debug(f"获取消息气泡{i}失败: {inner_exc}")

                    if len(result["message_bubbles"]) > 0:
                        break
            except Exception as exc:
                logger.debug(f"selector '{selector}' 失败: {exc}")

    except Exception as exc:
        logger.exception("dump当前页面出错")
        result["error"] = str(exc)

    return result
