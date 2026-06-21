"""临时dump工具：获取消息中心真实DOM和selector"""
import logging
from pathlib import Path
import time

logger = logging.getLogger(__name__)


def dump_conversations_action(page):
    """在browser_manager.with_page内执行的dump动作"""
    from platforms.goofish_urls import GOOFISH_URLS

    result = {
        "initial_url": page.url,
        "message_url": None,
        "in_iframe": False,
        "conversations_found": False,
        "dump_files": [],
        "conversation_items": [],
        "error": None,
    }

    try:
        # 1. 先回到首页
        logger.info("导航到首页...")
        page.goto(GOOFISH_URLS["home"], wait_until='domcontentloaded', timeout=15000)
        time.sleep(2)
        result["initial_url"] = page.url
        logger.info(f"当前URL: {page.url}")

        # 2. dump首页HTML
        dump_dir = Path("logs")
        dump_dir.mkdir(exist_ok=True)

        home_html = page.content()
        home_dump_path = dump_dir / "goofish_home_for_message_link.html"
        with open(home_dump_path, 'w', encoding='utf-8') as f:
            f.write(home_html)
        result["dump_files"].append(str(home_dump_path))
        logger.info(f"✅ 首页HTML已保存")

        # 3. 首页截图
        home_screenshot = dump_dir / "goofish_home_screenshot.png"
        page.screenshot(path=str(home_screenshot))
        result["dump_files"].append(str(home_screenshot))

        # 4. 查找"消息"链接
        logger.info("查找消息链接...")
        message_link = None

        try:
            links = page.locator('a').all()
            for link in links[:50]:
                try:
                    text = link.inner_text(timeout=500)
                    href = link.get_attribute('href')
                    if text and ('消息' in text or 'message' in text.lower()):
                        logger.info(f"找到消息链接: text='{text}', href='{href}'")
                        message_link = link
                        if href:
                            result["message_url"] = href
                        break
                except:
                    continue
        except Exception as exc:
            result["error"] = f"查找失败: {exc}"
            return result

        # 5. 点击进入消息中心
        if message_link:
            try:
                logger.info("点击消息链接...")
                message_link.click()
                page.wait_for_load_state('domcontentloaded', timeout=10000)
                time.sleep(3)
                result["message_url"] = page.url
                logger.info(f"✅ 消息中心URL: {page.url}")
            except Exception as exc:
                result["error"] = f"点击失败: {exc}"
                return result
        else:
            result["error"] = "首页未找到消息入口"
            return result

        # 6. dump消息中心HTML
        message_html = page.content()
        message_dump_path = dump_dir / "goofish_conversations_full.html"
        with open(message_dump_path, 'w', encoding='utf-8') as f:
            f.write(message_html)
        result["dump_files"].append(str(message_dump_path))
        logger.info(f"✅ 消息中心HTML已保存")

        # 7. 截图
        message_screenshot = dump_dir / "goofish_conversations_screenshot.png"
        page.screenshot(path=str(message_screenshot))
        result["dump_files"].append(str(message_screenshot))

        # 8. 查找会话项并dump前3个
        test_selectors = [
            '[class*="conversation"]',
            '[class*="message"]',
            '[class*="chat"]',
            '[class*="session"]',
            '[class*="item"]',
            'li',
        ]

        for selector in test_selectors:
            try:
                items = page.locator(selector).all()
                count = len(items)
                if 0 < count < 100:
                    logger.info(f"找到{count}个'{selector}'元素")

                    for i in range(min(3, count)):
                        try:
                            outer_html = items[i].evaluate('el => el.outerHTML')
                            preview = outer_html[:500] if len(outer_html) > 500 else outer_html
                            result["conversation_items"].append({
                                "index": i,
                                "selector": selector,
                                "html_preview": preview
                            })
                            logger.info(f"\n===== 项{i+1} ({selector}) =====\n{outer_html[:300]}...\n")

                            if i == 0:
                                first_item_path = dump_dir / "goofish_conversation_item_first.html"
                                with open(first_item_path, 'w', encoding='utf-8') as f:
                                    f.write(f"<!-- selector: {selector} -->\n{outer_html}")
                                result["dump_files"].append(str(first_item_path))
                        except:
                            continue

                    if len(result["conversation_items"]) > 0:
                        result["conversations_found"] = True
                        break
            except:
                continue

    except Exception as exc:
        logger.exception("dump过程出错")
        result["error"] = str(exc)

    return result
