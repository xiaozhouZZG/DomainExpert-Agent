"""
Dump 闲鱼 IM 发送框 DOM - 只侦察，不发送消息
"""
import json
import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from platforms.goofish_playwright import GoofishPlaywrightPlatform


def dump_im_send_box():
    """
    Dump 闲鱼 IM 页面发送框的真实 DOM

    操作：
    1. 打开 goofish.com/im
    2. 等待页面加载
    3. dump 输入框和发送按钮的 outerHTML
    4. 截图保存

    ⚠️ 只读 DOM，不发送任何消息
    """
    platform = GoofishPlaywrightPlatform()

    def action(page):
        print("=" * 80)
        print("开始 Dump 闲鱼 IM 发送框 DOM")
        print("=" * 80)
        print()

        # 导航到 IM 页面
        print("1. 导航到 goofish.com/im...")
        page.goto("https://goofish.com/im", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)  # 等待页面稳定

        # 截图1：IM 列表页
        screenshot_dir = Path("logs/im_dom_dump")
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        im_list_screenshot = screenshot_dir / "01_im_list.png"
        page.screenshot(path=str(im_list_screenshot))
        print(f"   ✓ 截图已保存: {im_list_screenshot}")
        print()

        # 尝试点击第一个会话（如果有）
        print("2. 尝试打开会话...")
        conversation_selectors = [
            'div[class*="conversation"]',
            'div[class*="item"]',
            'div[class*="chat"]',
            'div[class*="message"]',
            '.list-item',
            '.conversation-item'
        ]

        clicked = False
        for selector in conversation_selectors:
            try:
                elements = page.query_selector_all(selector)
                if elements and len(elements) > 0:
                    print(f"   找到 {len(elements)} 个元素 (selector: {selector})")
                    # 点击第一个
                    elements[0].click()
                    page.wait_for_timeout(2000)
                    clicked = True
                    print(f"   ✓ 已点击第一个会话")
                    break
            except Exception as e:
                continue

        if not clicked:
            print("   ⚠️  未找到会话列表，尝试继续...")
        print()

        # 截图2：会话详情页
        chat_detail_screenshot = screenshot_dir / "02_chat_detail.png"
        page.screenshot(path=str(chat_detail_screenshot))
        print(f"   ✓ 截图已保存: {chat_detail_screenshot}")
        print()

        # Dump 输入框 DOM
        print("3. Dump 输入框 DOM...")
        input_selectors = [
            'input[type="text"]',
            'textarea',
            'div[contenteditable="true"]',
            'div[role="textbox"]',
            '[placeholder*="输入"]',
            '[placeholder*="消息"]',
            '[placeholder*="说点什么"]',
        ]

        input_element = None
        input_selector_used = None

        for selector in input_selectors:
            try:
                elem = page.query_selector(selector)
                if elem:
                    input_element = elem
                    input_selector_used = selector
                    print(f"   ✓ 找到输入框 (selector: {selector})")
                    break
            except:
                continue

        if input_element:
            input_html = input_element.evaluate('el => el.outerHTML')
            print()
            print("=" * 80)
            print("输入框真实 outerHTML:")
            print("=" * 80)
            print(input_html)
            print()

            # 保存到文件
            input_html_file = screenshot_dir / "input_element.html"
            input_html_file.write_text(input_html, encoding='utf-8')
            print(f"✓ 输入框 HTML 已保存: {input_html_file}")
            print()
        else:
            print("   ✗ 未找到输入框")
            print()

        # Dump 发送按钮 DOM
        print("4. Dump 发送按钮 DOM...")
        button_selectors = [
            'button:has-text("发送")',
            'div:has-text("发送")',
            'span:has-text("发送")',
            'button[type="submit"]',
            'button[class*="send"]',
            'div[class*="send"]',
            '[aria-label*="发送"]',
        ]

        send_button = None
        button_selector_used = None

        for selector in button_selectors:
            try:
                elem = page.query_selector(selector)
                if elem:
                    send_button = elem
                    button_selector_used = selector
                    print(f"   ✓ 找到发送按钮 (selector: {selector})")
                    break
            except:
                continue

        if send_button:
            button_html = send_button.evaluate('el => el.outerHTML')
            print()
            print("=" * 80)
            print("发送按钮真实 outerHTML:")
            print("=" * 80)
            print(button_html)
            print()

            # 保存到文件
            button_html_file = screenshot_dir / "send_button.html"
            button_html_file.write_text(button_html, encoding='utf-8')
            print(f"✓ 发送按钮 HTML 已保存: {button_html_file}")
            print()
        else:
            print("   ✗ 未找到发送按钮")
            print()

        # 截图3：底部输入区特写
        if input_element or send_button:
            target_elem = input_element or send_button
            try:
                bounding_box = target_elem.bounding_box()
                if bounding_box:
                    # 扩大截图区域，包含周围元素
                    clip_box = {
                        'x': max(0, bounding_box['x'] - 50),
                        'y': max(0, bounding_box['y'] - 50),
                        'width': min(page.viewport_size['width'], bounding_box['width'] + 100),
                        'height': min(page.viewport_size['height'], bounding_box['height'] + 100)
                    }
                    input_area_screenshot = screenshot_dir / "03_input_area_closeup.png"
                    page.screenshot(path=str(input_area_screenshot), clip=clip_box)
                    print(f"✓ 输入区特写已保存: {input_area_screenshot}")
                    print()
            except Exception as e:
                print(f"   ⚠️  截取特写失败: {e}")
                print()

        # Dump 整个底部区域的 HTML
        print("5. Dump 底部输入区域完整 HTML...")
        bottom_area_selectors = [
            'div[class*="input"]',
            'div[class*="editor"]',
            'div[class*="compose"]',
            'div[class*="bottom"]',
            'form',
        ]

        for selector in bottom_area_selectors:
            try:
                elements = page.query_selector_all(selector)
                for elem in elements:
                    # 检查是否包含输入框
                    has_input = elem.query_selector('input, textarea, div[contenteditable="true"]')
                    if has_input:
                        area_html = elem.evaluate('el => el.outerHTML')

                        area_html_file = screenshot_dir / f"bottom_area_{selector.replace('[', '_').replace(']', '').replace('*', '_')}.html"
                        area_html_file.write_text(area_html, encoding='utf-8')
                        print(f"   ✓ 找到包含输入的区域 (selector: {selector})")
                        print(f"   ✓ 已保存: {area_html_file}")
                        break
            except:
                continue
        print()

        # 总结
        print("=" * 80)
        print("Dump 完成")
        print("=" * 80)
        print()
        print("文件保存位置:")
        print(f"  - 截图: {screenshot_dir}")
        print(f"  - HTML: {screenshot_dir}")
        print()

        return {
            "status": "ok",
            "input_found": input_element is not None,
            "button_found": send_button is not None,
            "input_selector": input_selector_used,
            "button_selector": button_selector_used,
            "screenshot_dir": str(screenshot_dir)
        }

    # 在 browser_worker 线程执行
    result = platform._run_with_page("dump_im_send_box", action)

    print()
    print("=" * 80)
    print("结果:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("=" * 80)

    return result


if __name__ == "__main__":
    dump_im_send_box()
