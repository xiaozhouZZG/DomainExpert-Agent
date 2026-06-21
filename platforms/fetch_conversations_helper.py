"""读取闲鱼会话列表"""
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def fetch_conversations_action(page) -> Dict[str, Any]:
    """读取/im页面的会话列表"""
    result = {
        "conversations": [],
        "error": None,
    }

    try:
        import time

        # 1. 确保在/im页面
        current_url = page.url
        logger.info(f"当前URL: {current_url}")

        if '/im' not in current_url:
            logger.info("不在/im页面，导航到消息中心...")
            page.goto("https://www.goofish.com/im", wait_until='domcontentloaded', timeout=15000)
            time.sleep(2)
            logger.info(f"已导航到: {page.url}")

        # 2. 检查是否在iframe中
        frames = page.frames
        target_frame = page

        if len(frames) > 1:
            for frame in frames:
                try:
                    test_html = frame.content()
                    if 'conversation-item' in test_html or '消息' in test_html:
                        target_frame = frame
                        logger.info("会话列表在iframe中")
                        break
                except:
                    continue

        # 3. 等待会话列表加载
        time.sleep(1)

        # 4. 使用稳定的selector遍历会话项
        # ✅ 不写死哈希，用前缀匹配
        conversation_items = target_frame.locator('div[class*="conversation-item"]').all()
        logger.info(f"找到 {len(conversation_items)} 个会话项")

        for idx, item in enumerate(conversation_items):
            try:
                # 提取会话信息
                conv_data = extract_conversation_item(item, idx)
                if conv_data:
                    result["conversations"].append(conv_data)
            except Exception as exc:
                logger.warning(f"提取会话项{idx}失败: {exc}")
                continue

        logger.info(f"成功提取 {len(result['conversations'])} 个会话")

    except Exception as exc:
        logger.exception("读取会话列表失败")
        result["error"] = str(exc)

    return result


def extract_conversation_item(item_locator, index: int) -> Dict[str, Any]:
    """从单个会话项提取信息"""
    try:
        # 获取整个item的HTML用于调试
        outer_html = item_locator.evaluate('el => el.outerHTML')

        # 1. 提取买家昵称
        # 特征：font-weight: 500, font-size: 14px
        nick_elements = item_locator.locator('div[style*="font-weight: 500"][style*="font-size: 14px"]').all()
        buyer_nick = None
        for elem in nick_elements:
            try:
                # 获取第一个子div的文本（昵称）
                text = elem.locator('div').first.inner_text(timeout=500)
                if text and len(text) < 50:  # 昵称不会太长
                    buyer_nick = text.strip()
                    break
            except:
                continue

        if not buyer_nick:
            logger.debug(f"会话项{index}未找到昵称，跳过")
            return None

        # 2. 判断是否是系统消息
        is_system = buyer_nick in ['通知消息', '系统消息']

        # 3. 提取订单状态
        order_status = None
        try:
            # 等待收货：[class*="order-wait"]
            wait_elem = item_locator.locator('[class*="order-wait"]').first
            if wait_elem.count() > 0:
                order_status = wait_elem.inner_text(timeout=500).strip()
        except:
            pass

        if not order_status:
            try:
                # 交易成功：[class*="order-success"]
                success_elem = item_locator.locator('[class*="order-success"]').first
                if success_elem.count() > 0:
                    order_status = success_elem.inner_text(timeout=500).strip()
            except:
                pass

        # 4. 提取最后一条消息预览
        # 特征：font-size: 12px, color: rgb(163, 163, 163)
        last_message = None
        try:
            message_elements = item_locator.locator('div[style*="font-size: 12px"][style*="color: rgb(163, 163, 163)"]').all()
            if len(message_elements) > 0:
                # 通常是第一个匹配的元素
                last_message = message_elements[0].inner_text(timeout=500).strip()
        except Exception as exc:
            logger.debug(f"提取消息预览失败: {exc}")

        # 5. 提取时间
        # 特征：font-size: 10px, color: rgb(163, 163, 163)
        time_str = None
        try:
            time_elements = item_locator.locator('div[style*="font-size: 10px"][style*="color: rgb(163, 163, 163)"]').all()
            if len(time_elements) > 0:
                time_str = time_elements[0].inner_text(timeout=500).strip()
        except Exception as exc:
            logger.debug(f"提取时间失败: {exc}")

        # 6. 提取头像URL
        avatar_url = None
        try:
            # 特征：width: 44px, height: 44px
            avatar_img = item_locator.locator('img[style*="width: 44px"][style*="height: 44px"]').first
            if avatar_img.count() > 0:
                avatar_url = avatar_img.get_attribute('src')
                if avatar_url and not avatar_url.startswith('http'):
                    avatar_url = f"https:{avatar_url}"
        except:
            pass

        # 7. 提取商品缩略图URL
        product_image = None
        try:
            # 特征：width: 50px, height: 50px
            product_img = item_locator.locator('img[style*="width: 50px"][style*="height: 50px"]').first
            if product_img.count() > 0:
                product_image = product_img.get_attribute('src')
                if product_image and not product_image.startswith('http'):
                    product_image = f"https:{product_image}"
        except:
            pass

        return {
            "index": index,
            "buyer_nick": buyer_nick,
            "is_system": is_system,
            "last_message": last_message or "",
            "time": time_str or "",
            "order_status": order_status,
            "avatar_url": avatar_url,
            "product_image": product_image,
        }

    except Exception as exc:
        logger.exception(f"提取会话项{index}失败")
        return None
