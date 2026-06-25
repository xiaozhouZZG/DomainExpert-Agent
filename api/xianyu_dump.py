"""临时dump API端点"""
import asyncio

from fastapi import APIRouter
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/debug/dump-conversations")
async def dump_conversations_dom():
    """dump消息中心的真实DOM结构"""
    try:
        from platforms.goofish_playwright import GoofishPlaywrightPlatform
        from platforms.dump_conversations_helper import dump_conversations_action

        platform = GoofishPlaywrightPlatform()

        def _do_dump():
            return platform.browser_manager.with_page("dump_conversations", dump_conversations_action)
        result = await asyncio.to_thread(_do_dump)

        return {
            "status": "success",
            "data": result,
            "detail": "DOM dump完成，请查看logs/目录"
        }
    except Exception as exc:
        logger.exception("dump失败")
        return {
            "status": "error",
            "detail": str(exc)
        }


@router.get("/debug/dump-current-page")
async def dump_current_page():
    """dump当前打开的页面（不导航，用户手动点到目标页后调用）"""
    try:
        from platforms.goofish_playwright import GoofishPlaywrightPlatform
        from platforms.dump_current_page_helper import dump_current_page_action

        platform = GoofishPlaywrightPlatform()

        def _do_dump_current():
            return platform.browser_manager.with_page("dump_current_page", dump_current_page_action)
        result = await asyncio.to_thread(_do_dump_current)

        return {
            "status": "success",
            "data": result,
            "detail": "当前页面dump完成，请查看logs/目录"
        }
    except Exception as exc:
        logger.exception("dump当前页面失败")
        return {
            "status": "error",
            "detail": str(exc)
        }


@router.get("/conversations")
async def get_conversations():
    """读取闲鱼会话列表"""
    try:
        from platforms.goofish_playwright import GoofishPlaywrightPlatform
        from platforms.fetch_conversations_helper import fetch_conversations_action
        from platforms.login_state import is_logged_in

        # 检查登录状态
        if not is_logged_in():
            return {
                "status": "not_logged_in",
                "results": [],
                "detail": "未登录，请先扫码登录"
            }

        platform = GoofishPlaywrightPlatform()

        def _do_fetch():
            return platform.browser_manager.with_page("fetch_conversations", fetch_conversations_action)
        result = await asyncio.to_thread(_do_fetch)

        if result.get("error"):
            return {
                "status": "error",
                "results": [],
                "detail": result["error"]
            }

        conversations = result.get("conversations", [])
        return {
            "status": "success",
            "results": conversations,
            "count": len(conversations),
            "detail": f"成功获取{len(conversations)}个会话"
        }

    except Exception as exc:
        logger.exception("读取会话列表失败")
        return {
            "status": "error",
            "results": [],
            "detail": str(exc)
        }
