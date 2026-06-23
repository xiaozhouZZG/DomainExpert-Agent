"""Playwright adapter for goofish.com.

This adapter keeps the integration conservative:
- real headed browser by default
- persistent local login state
- screenshots and execution telemetry for each key step
- no fake fallback data
- write operations remain approval-gated until selectors are confirmed
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar
from urllib.parse import quote_plus

from core.execution_bus import get_execution_event_bus
from .base import PlatformNotLoggedInError, PlatformNotReadyError
from .browser_manager import get_goofish_browser_manager, summarize_storage_state
from .browser_worker import get_browser_worker
from .goofish_selectors import GOOFISH_SELECTORS, GOOFISH_URLS

logger = logging.getLogger(__name__)

T = TypeVar("T")


class GoofishPlaywrightPlatform:
    """Browser-driven Goofish adapter."""

    def __init__(
        self,
        storage_state_path: str = "data/browser_state/goofish.json",
        user_data_dir: str = "data/browser_state/goofish_profile",
        screenshot_dir: str = "logs/playwright",
        headless: bool = False,  # ✅ 统一使用headed（真实浏览器）
        slow_mo_ms: int = 500,
    ) -> None:
        self.storage_state_path = Path(storage_state_path)
        self.user_data_dir = Path(user_data_dir)
        self.screenshot_dir = Path(screenshot_dir)

        # ✅ 统一使用headed模式（常驻真实浏览器）
        self.headless = False
        logger.info("Using headed mode (persistent real browser) to avoid anti-scraping")

        self.slow_mo_ms = slow_mo_ms
        self.event_bus = get_execution_event_bus()
        self.browser_manager = get_goofish_browser_manager(
            user_data_dir=str(self.user_data_dir),
            storage_state_path=str(self.storage_state_path),
            headless=self.headless,
            slow_mo_ms=slow_mo_ms,
        )

        self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def login(self) -> Dict[str, Any]:
        """Open Goofish and let the user complete login manually."""

        def action(page):
            self._emit_event("browser", "login", "running", detail="opening real Goofish browser", data_source="goofish")
            page.goto(GOOFISH_URLS["home"], wait_until="domcontentloaded")
            screenshot_path = self._capture_step_screenshot(page, "login-home")
            self._emit_event(
                "browser",
                "login",
                "running",
                screenshot_path=screenshot_path,
                detail="Goofish home opened",
                data_source="goofish",
            )
            self._human_delay()
            self._pause_if_challenge(page)
            self.browser_manager.save_storage_state()
            screenshot_path = self._capture_step_screenshot(page, "login-saved")
            self._emit_event(
                "browser",
                "login",
                "success",
                screenshot_path=screenshot_path,
                detail="Goofish login state saved",
                data_source="goofish",
            )
            return {
                "status": "success",
                "storage_state": str(self.storage_state_path),
                "headed": not self.headless,
            }

        return self._run_with_page("login", action)

    def login_interactive(self, max_wait_seconds: int = 300) -> Dict[str, Any]:
        """Open a headed browser and let the user finish login locally."""
        started_at = time.time()
        logger.info("opening real Goofish browser for interactive login (forced headed mode)")

        # 强制清理可能失效的登录态，确保重新登录
        if self.storage_state_path.exists():
            logger.warning("removing existing storage_state to force fresh login")
            self.storage_state_path.unlink()

        # ✅ 强制关闭并重启browser_manager，确保使用全新context
        logger.info("stopping existing browser_manager to force fresh context")
        self.browser_manager.stop()

        # ✅ 已统一使用headed模式，不需要手动切换
        logger.info("using headed browser mode for login (already set in __init__)")

        self._emit_event(
            "browser",
            "login_interactive",
            "start",
            detail="opening headed browser for QR code login",
            data_source="goofish",
        )
        try:
            def action(page):
                self._goto_domcontentloaded_or_body(page, GOOFISH_URLS["home"], "login_interactive")
                self._open_login_prompt_if_needed(page)
                self._emit_event(
                    "browser",
                    "login_interactive",
                    "running",
                    detail="browser opened, waiting for manual login",
                    data_source="goofish",
                )
                last_save_at = 0.0
                last_state: Dict[str, Any] = {
                    "logged_in": False,
                    "detail": "waiting for manual login",
                }

                while time.time() - started_at < max_wait_seconds:
                    try:
                        if page.is_closed():
                            break
                        if time.time() - last_save_at >= 5:
                            self.browser_manager.save_storage_state()
                            last_save_at = time.time()
                            last_state = self._detect_login_state(page)
                            if last_state["logged_in"]:
                                self._minimize_browser_window(page)
                                self._emit_event(
                                    "browser",
                                    "login_interactive",
                                    "success",
                                    duration_ms=(time.time() - started_at) * 1000,
                                    detail="local Goofish login state saved",
                                    data_source="goofish",
                                )
                                return {
                                    "status": "success",
                                    "storage_state": str(self.storage_state_path),
                                    "user_data_dir": str(self.user_data_dir),
                                    "elapsed_seconds": round(time.time() - started_at, 1),
                                    "login_state": last_state,
                                }
                        time.sleep(2)
                    except Exception as exc:
                        raise RuntimeError(f"Goofish interactive login interrupted: {exc}") from exc

                raise PlatformNotLoggedInError(
                    "Goofish login was not completed before timeout; "
                    f"last_state={last_state.get('detail', 'unknown')}"
                )

            # ✅ 登录通过browser_manager.with_page执行，自动在worker线程运行
            result = self.browser_manager.with_page("login_interactive", action)

            # ✅ 登录成功后：更新单一真相源
            from platforms.login_state import mark_login_success
            mark_login_success("扫码登录成功")
            logger.info("login success marked in single source of truth")

            # ✅ 暂时关闭最小化窗口（避免跨线程访问page/context破坏greenlet）
            # 窗口保持可见，先确保登录→fetch链路不再跨线程崩溃
            logger.info("login completed, browser window remains visible (minimize disabled to avoid greenlet corruption)")

            # ✅ 保持browser_manager单例常驻，不再关闭
            context_id = id(self.browser_manager._context) if self.browser_manager._context else None
            logger.info(
                "✅ LOGIN context kept alive for future operations, context_id=%s (will be reused by fetch)",
                context_id
            )

            return result
        except Exception as exc:
            # ✅ 登录失败：更新单一真相源
            from platforms.login_state import mark_not_logged_in
            mark_not_logged_in(f"登录失败: {exc}")

            # ✅ 已统一使用headed，不需要恢复headless设置
            logger.warning("login failed: %s", exc)
            self._emit_event(
                "browser",
                "login_interactive",
                "failure",
                duration_ms=(time.time() - started_at) * 1000,
                detail="interactive login failed",
                error=str(exc),
                data_source="goofish",
            )
            raise RuntimeError(
                f"Goofish interactive login failed: {exc}"
            ) from exc

    def read_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Read buyer messages from the real Goofish messages page."""
        if not self.storage_state_path.exists():
            raise PlatformNotLoggedInError("goofish login state not found; please login first")

        def action(page):
            page.goto(GOOFISH_URLS["messages"], wait_until="domcontentloaded")
            screenshot_path = self._capture_step_screenshot(page, "messages-home")
            self._emit_event(
                "browser",
                "read_messages",
                "running",
                screenshot_path=screenshot_path,
                detail="Goofish messages page opened",
                data_source="goofish",
            )
            self._pause_if_challenge(page)

            selector = GOOFISH_SELECTORS.get("message_thread")
            if not selector:
                raise PlatformNotReadyError("message_thread selector needs confirmation from a real logged-in page")

            threads_locator = page.locator(selector)
            thread_count = threads_locator.count()
            if thread_count == 0:
                if self._looks_logged_out(page):
                    raise PlatformNotLoggedInError("please scan QR code and login to Goofish first")
                self._emit_event(
                    "browser",
                    "read_messages",
                    "success",
                    screenshot_path=screenshot_path,
                    detail="no readable buyer messages on the current page",
                    data_source="goofish",
                    metadata={"count": 0},
                )
                return []

            text_selector = GOOFISH_SELECTORS.get("message_text")
            if not text_selector:
                raise PlatformNotReadyError("message_text selector needs confirmation from a real logged-in page")

            messages: List[Dict[str, Any]] = []
            for idx in range(min(thread_count, limit)):
                thread = threads_locator.nth(idx)
                text = thread.locator(text_selector).inner_text().strip()
                messages.append(
                    {
                        "conversation_id": f"goofish-{idx}",
                        "content": text,
                        "source": "goofish",
                        "platform": "goofish",
                        "source_label": "real",
                        "received_at": datetime.now().isoformat(),
                    }
                )
                self._human_delay()

            screenshot_path = self._capture_step_screenshot(page, "messages-read")
            self._emit_event(
                "browser",
                "read_messages",
                "success",
                screenshot_path=screenshot_path,
                detail=f"read {len(messages)} buyer messages",
                data_source="goofish",
                metadata={"count": len(messages)},
            )
            return messages

        return self._run_with_page("read_messages", action)

    def fetch_competitor(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch public competitor data from the real Goofish search results page."""
        if not self.storage_state_path.exists():
            from platforms.login_state import mark_not_logged_in
            mark_not_logged_in("goofish.json不存在")
            raise PlatformNotLoggedInError("goofish login state not found; please login first")

        # ✅ 打印context_id，验证是否复用登录的context
        context_id = id(self.browser_manager._context) if self.browser_manager._context else None
        logger.info(
            "✅ FETCH starting with context_id=%s (should match LOGIN context_id)",
            context_id
        )

        def action(page):
            # ✅ 暂时用直接goto（跨线程修好后再优化搜索框selector）
            search_url = GOOFISH_URLS["search"].format(keyword=quote_plus(keyword))
            logger.info("navigating to search URL: %s", search_url)
            self._goto_domcontentloaded_or_body(page, search_url, "fetch_competitor")
            time.sleep(random.uniform(1.0, 2.0))  # 随机延迟

            screenshot_path = self._capture_step_screenshot(page, "competitor-search-home")

            self._emit_event(
                "browser",
                "fetch_competitor",
                "running",
                screenshot_path=screenshot_path,
                detail=f"Goofish search initiated for keyword: {keyword}",
                data_source="goofish",
                metadata={"keyword": keyword},
            )
            self._pause_if_challenge(page)
            self._wait_for_search_results(page, keyword)

            cards_locator = page.locator(GOOFISH_SELECTORS["search_result_card"])
            card_count = cards_locator.count()
            if card_count == 0:
                if self._looks_logged_out(page):
                    # ✅ 检测到未登录，更新真相源
                    from platforms.login_state import mark_not_logged_in
                    mark_not_logged_in("页面显示未登录")
                    raise PlatformNotLoggedInError("please scan QR code and login to Goofish first")
                self._emit_event(
                    "browser",
                    "fetch_competitor",
                    "success",
                    screenshot_path=screenshot_path,
                    detail=f"no public competitor data found for keyword: {keyword}",
                    data_source="goofish",
                    metadata={"keyword": keyword, "count": 0},
                )
                # ✅ 没有结果但已登录，更新真相源
                from platforms.login_state import mark_fetch_success
                mark_fetch_success(f"搜索'{keyword}'成功，但无结果")
                return []

            results: List[Dict[str, Any]] = []
            for idx in range(min(card_count, limit)):
                card = cards_locator.nth(idx)
                parsed = self._extract_competitor_card(card, keyword, idx)
                if parsed["title"] or parsed["price"]:
                    results.append(parsed)
                self._human_delay()

            screenshot_path = self._capture_step_screenshot(page, "competitor-results")
            self._emit_event(
                "browser",
                "fetch_competitor",
                "success",
                screenshot_path=screenshot_path,
                detail=f"captured {len(results)} competitor cards",
                data_source="goofish",
                metadata={"keyword": keyword, "count": len(results)},
            )
            # ✅ 抓取成功，更新真相源
            from platforms.login_state import mark_fetch_success
            mark_fetch_success(f"成功抓取{len(results)}个竞品")
            return results

        return self._run_with_page("fetch_competitor", action)

    def read_listings(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Read the seller's real on-sale listings from Goofish."""
        if not self.storage_state_path.exists():
            raise PlatformNotLoggedInError("goofish login state not found; please login first")

        def action(page):
            try:
                page = self._open_personal_page_from_home(page)
                listing_area = self._wait_for_seller_listing_area(page)
            except Exception as exc:
                raise PlatformNotReadyError(
                    f"seller listings page is not reachable through the real Goofish UI; {exc}"
                ) from exc

            self._human_delay(min_seconds=4.0, max_seconds=7.0)
            screenshot_path = self._capture_step_screenshot(page, "seller-listings")
            self._emit_event(
                "browser",
                "read_listings",
                "running",
                screenshot_path=screenshot_path,
                detail=f"Goofish seller listings page opened: {page.url}",
                data_source="goofish",
            )
            self._pause_if_challenge(page)

            if self._looks_logged_out(page):
                raise PlatformNotLoggedInError("please scan QR code and login to Goofish first")

            if self._seller_listing_page_is_empty(page):
                self._emit_event(
                    "browser",
                    "read_listings",
                    "success",
                    screenshot_path=screenshot_path,
                    detail="real Goofish seller page shows no on-sale listings",
                    data_source="goofish",
                    metadata={"count": 0, "url": page.url},
                )
                return []

            detected_cards = self._extract_personal_listing_cards(page, limit)
            if detected_cards:
                self._emit_event(
                    "browser",
                    "read_listings",
                    "success",
                    screenshot_path=screenshot_path,
                    detail=f"read {len(detected_cards)} seller listings from confirmed personal page structure",
                    data_source="goofish",
                    metadata={"count": len(detected_cards), "url": page.url},
                )
                return detected_cards

            card_selector = GOOFISH_SELECTORS.get("seller_listing_card")
            if not card_selector:
                diagnostic = self._listing_page_diagnostic(page)
                raise PlatformNotReadyError(
                    "seller_listing_card selector needs confirmation from a real logged-in seller page; "
                    f"url={diagnostic['url']} title={diagnostic['title']} "
                    f"text_sample={diagnostic['text_sample']!r}"
                )

            cards = page.locator(card_selector)
            try:
                cards.first.wait_for(state="visible", timeout=60000)
            except Exception:
                pass
            card_count = cards.count()
            if card_count == 0:
                diagnostic = self._listing_page_diagnostic(page)
                self._emit_event(
                    "browser",
                    "read_listings",
                    "success",
                    screenshot_path=screenshot_path,
                    detail="no seller listing cards matched on the current real page",
                    data_source="goofish",
                    metadata={"count": 0, "url": diagnostic["url"]},
                )
                return []

            listings: List[Dict[str, Any]] = []
            for idx in range(min(card_count, limit)):
                card = cards.nth(idx)
                raw_text = card.inner_text().strip()
                item_url = self._extract_attr(card, GOOFISH_SELECTORS.get("seller_listing_link"), "href")
                item_id = self._extract_listing_id(item_url, raw_text, idx)
                listings.append(
                    {
                        "item_id": item_id,
                        "title": self._extract_text(card, GOOFISH_SELECTORS.get("seller_listing_title")) or raw_text[:120],
                        "price": self._extract_text(card, GOOFISH_SELECTORS.get("seller_listing_price")),
                        "view_count": None,
                        "want_count": None,
                        "published_at": self._extract_text(card, GOOFISH_SELECTORS.get("seller_listing_published_at")),
                        "item_url": item_url,
                        "status": "on_sale",
                        "platform": "goofish",
                        "source_label": "goofish",
                        "raw_text": raw_text,
                    }
                )
                self._human_delay()

            self._emit_event(
                "browser",
                "read_listings",
                "success",
                screenshot_path=screenshot_path,
                detail=f"read {len(listings)} seller listings",
                data_source="goofish",
                metadata={"count": len(listings), "url": page.url},
            )
            return listings

        return self._run_with_page("read_listings", action)

    def ensure_im_ready(self, page, max_heal: int = 2) -> Dict[str, Any]:
        """
        就绪闸门：强制收敛页面到"可读状态"，不行就自愈

        策略：
        1. 检查 page/context 是否活着
        2. 如果不在 /im 页面，导航过去（commit，不等 domcontentloaded）
        3. 检测登录态（localStorage）
        4. 轮询等会话列表出现（15s）
        5. 没出现就 reload 自愈，最多 max_heal 次

        Returns:
            {
                "status": "ready" | "need_login" | "failed",
                "detail": "描述",
                "screenshot": "截图路径（失败时）"
            }
        """
        import time
        from pathlib import Path
        import datetime

        IM_URL = "https://www.goofish.com/im"
        CONVERSATION_SELECTOR = '[class*="conversation-item"]'

        logger.info(f"ensure_im_ready: 开始页面就绪检查，max_heal={max_heal}")

        for heal_round in range(max_heal + 1):
            try:
                logger.info(f"ensure_im_ready: 第 {heal_round + 1}/{max_heal + 1} 轮检查")

                # 步骤1: 检查 page/context 是否活着
                try:
                    current_url = page.url
                    logger.info(f"ensure_im_ready: 当前 URL = {current_url}")
                except Exception as e:
                    logger.error(f"ensure_im_ready: page/context 已死: {e}")
                    return {
                        "status": "failed",
                        "detail": f"Page/context is dead: {e}",
                        "screenshot": None
                    }

                # 步骤2: 如果不在 /im 页面，导航过去（commit，不等 domcontentloaded）
                if "/im" not in current_url:
                    logger.info(f"ensure_im_ready: 不在 IM 页面，导航到 {IM_URL}")
                    try:
                        page.goto(IM_URL, wait_until="commit", timeout=20000)
                        logger.info("ensure_im_ready: 导航到 IM 页面完成（commit）")
                        time.sleep(3)  # 给页面更多渲染时间
                    except Exception as e:
                        logger.warning(f"ensure_im_ready: 导航失败: {e}")
                        if heal_round < max_heal:
                            logger.info("ensure_im_ready: 准备重试")
                            continue
                        else:
                            return {
                                "status": "failed",
                                "detail": f"Navigation failed after {max_heal} attempts: {e}",
                                "screenshot": self._save_failure_screenshot(page, "nav_failed")
                            }

                # 步骤3: 检测登录态
                # 方式1: 检查 localStorage（快速预检）
                try:
                    local_storage_count = page.evaluate("() => Object.keys(localStorage).length")
                    logger.info(f"ensure_im_ready: localStorage 条目数 = {local_storage_count}")
                except Exception as e:
                    logger.warning(f"ensure_im_ready: 检测 localStorage 失败: {e}")
                    local_storage_count = 0

                # 步骤4: 轮询等会话列表出现（20s）
                # 优先信任会话列表这个强信号，有会话列表 = 登录正常
                logger.info(f"ensure_im_ready: 轮询等待会话列表出现（{CONVERSATION_SELECTOR}）")
                poll_timeout = 20
                poll_interval = 0.5
                elapsed = 0

                while elapsed < poll_timeout:
                    try:
                        conversation_items = page.locator(CONVERSATION_SELECTOR).all()
                        if len(conversation_items) > 0:
                            logger.info(f"ensure_im_ready: ✓ 会话列表已出现，找到 {len(conversation_items)} 项")
                            return {
                                "status": "ready",
                                "detail": f"Conversation list ready with {len(conversation_items)} items",
                                "screenshot": None
                            }
                    except Exception as e:
                        logger.warning(f"ensure_im_ready: 查询会话列表失败: {e}")

                    time.sleep(poll_interval)
                    elapsed += poll_interval

                logger.warning(f"ensure_im_ready: 会话列表超时未出现（{poll_timeout}s）")

                # 步骤5: 会话列表找不到，才检查是否是登录问题
                try:
                    # 等待页面稍微渲染
                    time.sleep(1)

                    # 检查是否有登录二维码或登录按钮
                    login_qrcode = page.locator('[class*="qrcode"], [class*="login-qrcode"], [class*="scan-code"]').count()
                    login_button = page.locator('text=/扫码登录|立即登录|请登录/i').count()

                    logger.info(f"ensure_im_ready: 登录元素检测 - 二维码:{login_qrcode} 登录按钮:{login_button}")

                    # 只有明确的登录页特征（二维码或明确的登录按钮）才判定为 need_login
                    if login_qrcode > 0 or login_button > 0:
                        logger.warning("ensure_im_ready: 检测到登录页面元素，未登录")
                        return {
                            "status": "need_login",
                            "detail": f"Login page detected (qrcode:{login_qrcode}, button:{login_button})",
                            "screenshot": self._save_failure_screenshot(page, "need_login")
                        }
                except Exception as e:
                    logger.warning(f"ensure_im_ready: 检测登录元素失败: {e}")

                # 步骤6: 自愈重试 - reload
                if heal_round < max_heal:
                    logger.info(f"ensure_im_ready: 自愈 - reload 页面")
                    try:
                        page.reload(wait_until="commit", timeout=20000)
                        logger.info("ensure_im_ready: reload 完成")
                        time.sleep(3)  # 等待页面稳定
                    except Exception as e:
                        logger.warning(f"ensure_im_ready: reload 失败: {e}")
                else:
                    # 最后一轮仍失败
                    logger.error(f"ensure_im_ready: 自愈 {max_heal} 轮后仍未找到会话列表")
                    return {
                        "status": "failed",
                        "detail": f"Conversation list not found after {max_heal} heal attempts",
                        "screenshot": self._save_failure_screenshot(page, "no_conversation_list")
                    }

            except Exception as e:
                logger.exception(f"ensure_im_ready: 第 {heal_round + 1} 轮出现异常")
                if heal_round >= max_heal:
                    return {
                        "status": "failed",
                        "detail": f"Unexpected error: {str(e)}",
                        "screenshot": self._save_failure_screenshot(page, "exception")
                    }

        return {
            "status": "failed",
            "detail": "Max heal rounds exceeded",
            "screenshot": None
        }

    def _save_failure_screenshot(self, page, reason: str) -> str:
        """保存失败截图"""
        try:
            from pathlib import Path
            import datetime

            screenshot_dir = Path("logs/ensure_im_ready_failures")
            screenshot_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = screenshot_dir / f"{reason}_{timestamp}.png"

            page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info(f"_save_failure_screenshot: 已保存 {screenshot_path}")
            return str(screenshot_path)
        except Exception as e:
            logger.warning(f"_save_failure_screenshot: 截图失败: {e}")
            return None

    def poll_unread_conversations(
        self,
        on_unread_message: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        轮询未读会话（影子模式：只读不发）

        遍历会话列表，找出有未读消息且最后一条是买家消息的会话。
        【绝不发送消息，只读取+记录】

        Args:
            on_unread_message: 回调函数，在点进会话、读到买家消息的那一刻就地调用。
                回调接收单个参数: {
                    "buyer_name": str,
                    "last_buyer_msg": str,
                    "unread_count": int,
                    "needs_reply": bool,
                    "last_msg_direction": "left" | "right"
                }
                如果提供回调，则在此函数内完成所有处理（检索、决策、存草稿等），
                避免二次poll抓空。

        Returns:
            [
                {
                    "buyer_name": "买家昵称",
                    "last_buyer_msg": "买家最新问题",
                    "unread_count": 3,
                    "needs_reply": true
                },
                ...
            ]
            注意：如果提供了 on_unread_message 回调，返回的结果可能不完整（回调自行处理），
            调用方应检查回调的副作用而非依赖返回值。
        """
        # 系统号黑名单（宁漏勿杀，只过滤明确的系统/平台号）
        SYSTEM_ACCOUNT_BLACKLIST = [
            "通知消息",
            "官方代充",
            "闲鱼小蜜",
            "系统消息",
            "平台通知",
        ]

        def action(page):
            import time

            results = []

            try:
                # 步骤0: 先过就绪闸门
                logger.info("poll_unread_conversations: 调用就绪闸门")
                readiness = self.ensure_im_ready(page, max_heal=2)
                logger.info(f"poll_unread_conversations: 就绪闸门返回: {readiness}")

                if readiness["status"] != "ready":
                    # 页面未就绪，直接返回带状态的结果
                    logger.warning(f"poll_unread_conversations: 页面未就绪，状态={readiness['status']}")
                    return {
                        "status": readiness["status"],
                        "detail": readiness["detail"],
                        "screenshot": readiness.get("screenshot"),
                        "conversations": []
                    }

                # 页面已就绪，开始读取会话
                logger.info("poll_unread_conversations: 页面已就绪，开始读取会话")

                # 获取会话列表（闸门已确保会话列表存在）
                conversation_items = []
                try:
                    conversation_items = page.locator('[class*="conversation-item"]').all()
                    logger.info(f"poll_unread_conversations: 找到 {len(conversation_items)} 个会话项")
                except Exception as e:
                    # 如果是 Execution context destroyed，重试一次
                    if "Execution context was destroyed" in str(e):
                        logger.warning(f"poll_unread_conversations: Execution context destroyed，重试")
                        time.sleep(1)
                        try:
                            conversation_items = page.locator('[class*="conversation-item"]').all()
                            logger.info(f"poll_unread_conversations: 重试成功，找到 {len(conversation_items)} 个会话项")
                        except Exception as e2:
                            logger.error(f"poll_unread_conversations: 重试仍失败: {e2}")
                            return {
                                "status": "failed",
                                "detail": f"Locator retry failed: {str(e2)}",
                                "screenshot": self._save_failure_screenshot(page, "locator_failed"),
                                "conversations": []
                            }
                    else:
                        logger.error(f"poll_unread_conversations: 获取会话列表失败: {e}")
                        return {
                            "status": "failed",
                            "detail": f"Failed to get conversation items: {str(e)}",
                            "screenshot": self._save_failure_screenshot(page, "get_items_failed"),
                            "conversations": []
                        }

                if len(conversation_items) == 0:
                    logger.info("poll_unread_conversations: 真实 0 个会话（闸门已确保页面正常）")
                    return {
                        "status": "ready",
                        "detail": "No conversations found (empty state)",
                        "screenshot": None,
                        "conversations": []
                    }

                # 步骤2: 遍历会话项，筛选有未读的
                logger.info(f"poll_unread_conversations: 开始遍历 {len(conversation_items)} 个会话")

                # 【DOM dump 调试】找到第一个有未读的会话项，打印其 innerHTML 结构
                # 一次性使用，定位到昵称独立节点后改为精确 selector
                dom_dumped = False

                for idx, item in enumerate(conversation_items):
                    try:
                        # 检查未读角标
                        badge = item.locator('.ant-badge-count').first
                        unread_count = 0

                        try:
                            if badge.is_visible(timeout=500):
                                badge_text = badge.text_content()
                                if badge_text and badge_text.isdigit():
                                    unread_count = int(badge_text)
                        except:
                            pass

                        # 无未读消息，跳过
                        if unread_count == 0:
                            continue

                        # === DOM dump（仅第一次命中未读时）===
                        if not dom_dumped:
                            try:
                                inner_html = item.evaluate("el => el.innerHTML")
                                # 限制长度，避免日志过大
                                logger.info("=" * 80)
                                logger.info(f"poll_unread_conversations: 【DOM DUMP】会话项 [{idx}] innerHTML:")
                                logger.info(inner_html[:3000])
                                logger.info("=" * 80)

                                # 打印直接子节点 class 列表，帮助快速定位昵称节点
                                child_classes = item.evaluate("""
                                    el => Array.from(el.querySelectorAll('*')).map(node => ({
                                        tag: node.tagName,
                                        class: node.className,
                                        text: (node.textContent || '').substring(0, 40)
                                    })).slice(0, 30)
                                """)
                                logger.info(f"poll_unread_conversations: 【DOM DUMP】子节点 class 列表:")
                                for i, c in enumerate(child_classes):
                                    logger.info(f"  [{i}] <{c['tag']} class='{c['class']}'> text='{c['text']}'")
                                logger.info("=" * 80)
                                dom_dumped = True
                            except Exception as dump_err:
                                logger.warning(f"poll_unread_conversations: DOM DUMP 失败: {dump_err}")

                        # === 提取买家昵称（基于 DOM DUMP 真实结构）===
                        # DOM 结构: 会话项内有多个无 class 的 div，按内联 style 区分:
                        #   - 昵称 div: style 含 "max-width: 180px" + "font-weight: 500"
                        #   - 预览 div: style 含 "max-width: 165px" + "font-size: 12px"
                        #   - 时间 div: style 含 "font-size: 10px"
                        # 昵称是第 1 个带 max-width: 180px 的 div
                        buyer_name = None
                        try:
                            # 优先: 找内联 style 含 max-width: 180px 的 div（昵称专用）
                            name_divs = item.locator('div[style*="max-width: 180px"]').all()
                            if len(name_divs) > 0:
                                buyer_name = name_divs[0].text_content().strip()
                            else:
                                # 降级方案: 找 font-weight: 500 的 div
                                name_divs = item.locator('div[style*="font-weight: 500"]').all()
                                if len(name_divs) > 0:
                                    buyer_name = name_divs[0].text_content().strip()
                        except Exception as name_err:
                            logger.warning(f"poll_unread_conversations: [{idx}] 精确提取昵称失败: {name_err}")

                        # 再降级: 用旧逻辑（text_content + 正则切开头数字）
                        if not buyer_name:
                            item_text = item.text_content()
                            buyer_name = item_text.strip()
                            import re
                            buyer_name = re.sub(r'^\d+\s*', '', buyer_name)
                            logger.warning(f"poll_unread_conversations: [{idx}] 精确提取失败，降级用 text_content: '{buyer_name}'")

                        logger.info(f"poll_unread_conversations: [{idx}] 提取到干净昵称: '{buyer_name}' (未读: {unread_count})")

                        # 系统号过滤
                        is_system = any(keyword in buyer_name for keyword in SYSTEM_ACCOUNT_BLACKLIST)
                        if is_system:
                            logger.info(f"poll_unread_conversations: [{idx}] 跳过系统号: '{buyer_name}'")
                            continue

                        # 步骤3: 点进会话，检查最后一条消息方向
                        logger.info(f"poll_unread_conversations: [{idx}] 点击进入会话: '{buyer_name}'")
                        item.click()
                        time.sleep(2)

                        # 等待消息区出现
                        message_bubbles = page.locator('[class*="message-text"]').all()
                        if len(message_bubbles) == 0:
                            logger.warning(f"poll_unread_conversations: [{idx}] 未找到消息气泡，跳过")
                            continue

                        logger.info(f"poll_unread_conversations: [{idx}] 找到 {len(message_bubbles)} 条消息")

                        # 检查最后一条消息方向
                        last_bubble = message_bubbles[-1]
                        last_bubble_html = last_bubble.evaluate("el => el.outerHTML")

                        # 判断是买家消息还是自己消息
                        is_buyer_msg = "message-text-left" in last_bubble_html
                        is_self_msg = "message-text-right" in last_bubble_html

                        if is_buyer_msg:
                            last_buyer_msg = last_bubble.text_content()
                            last_msg_direction = "left"
                            logger.info(f"poll_unread_conversations: [{idx}] ✓ 买家在等回复: '{last_buyer_msg}'")

                            conv_data = {
                                "buyer_name": buyer_name,
                                "last_buyer_msg": last_buyer_msg,
                                "unread_count": unread_count,
                                "needs_reply": True,
                                "last_msg_direction": last_msg_direction
                            }
                            results.append(conv_data)

                            # 【一趟扫描就地处理】回调在点进会话、读到消息的那一刻立刻执行
                            # 避免二次poll抓空（点开会话清未读是不可逆的）
                            if on_unread_message is not None:
                                try:
                                    logger.info(f"poll_unread_conversations: [{idx}] 就地调用回调处理: '{buyer_name}'")
                                    on_unread_message(conv_data)
                                    logger.info(f"poll_unread_conversations: [{idx}] 回调处理完成: '{buyer_name}'")
                                except Exception as cb_err:
                                    logger.error(f"poll_unread_conversations: [{idx}] 回调处理失败: '{buyer_name}' - {cb_err}")

                        elif is_self_msg:
                            last_msg_direction = "right"
                            logger.info(f"poll_unread_conversations: [{idx}] 最后一条是自己的消息，已回复")
                            results.append({
                                "buyer_name": buyer_name,
                                "last_buyer_msg": None,
                                "unread_count": unread_count,
                                "needs_reply": False,
                                "last_msg_direction": last_msg_direction
                            })

                        else:
                            logger.warning(f"poll_unread_conversations: [{idx}] 无法判断消息方向")

                        # === 副作用日志：未读角标已清 ===
                        try:
                            time.sleep(0.5)
                            badge_after = item.locator('.ant-badge-count').first
                            badge_after_visible = False
                            try:
                                badge_after_visible = badge_after.is_visible(timeout=300)
                            except:
                                pass

                            if not badge_after_visible:
                                logger.info(f"poll_unread_conversations: [{idx}] 未读角标已清（会话 '{buyer_name}'）— 下次 poll 该会话 unread 将为 0")
                            else:
                                badge_after_text = badge_after.text_content()
                                logger.info(f"poll_unread_conversations: [{idx}] 未读角标仍可见: '{badge_after_text}'（会话 '{buyer_name}'）")
                        except Exception as badge_err:
                            logger.warning(f"poll_unread_conversations: [{idx}] 检查未读角标状态失败: {badge_err}")

                    except Exception as e:
                        logger.warning(f"poll_unread_conversations: [{idx}] 处理会话时出错: {e}")
                        continue

                logger.info(f"poll_unread_conversations: 完成，找到 {len(results)} 个会话需要处理")
                return {
                    "status": "ready",
                    "detail": f"Found {len(results)} conversations",
                    "screenshot": None,
                    "conversations": results
                }

            except Exception as e:
                logger.exception("poll_unread_conversations unexpected error")
                return {
                    "status": "failed",
                    "detail": f"Unexpected error: {str(e)}",
                    "screenshot": self._save_failure_screenshot(page, "unexpected_error"),
                    "conversations": []
                }

        # 在 browser_worker 线程内执行
        return self._run_with_page("poll_unread_conversations", action)

    def send_reply(
        self,
        conversation_id: str,
        content: str,
        approval_id: Optional[str] = None,
        target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        发送回复消息到闲鱼会话（全自动：选会话 + 发送 + 硬校验）

        Args:
            conversation_id: 会话ID（占位符）
            content: 消息内容
            approval_id: 审批ID（必需）
            target: 买家昵称，用于自动选择会话

        Returns:
            {"status": "sent", ...} 或 {"status": "failed", "uncertain"...}
        """
        if not approval_id:
            return {"status": "approval_required", "action": "send_reply"}

        def action(page):
            import time

            result = {
                "status": "failed",
                "conversation_id": conversation_id,
                "content": content,
                "target": target,
                "method": None,
                "evidence": {}
            }

            try:
                current_url = page.url
                logger.info(f"send_reply: 当前URL = {current_url}")

                # 步骤1: 自动选择会话（防止发错人）
                if target:
                    logger.info(f"send_reply: 自动选择会话 target='{target}'")

                    # 优先：复用现有页面，检查是否已有会话列表
                    conversation_items = page.locator('[class*="conversation-item"]').all()
                    logger.info(f"send_reply: 当前页面找到 {len(conversation_items)} 个会话项")

                    # 如果没有会话列表，需要导航到 IM 页面
                    if len(conversation_items) == 0:
                        logger.info("send_reply: 需要导航到 IM 页面")

                        # 使用带 www 的同域 URL，避免 301 重定向
                        try:
                            # wait_until="commit" 不等整页加载
                            page.goto("https://www.goofish.com/im", wait_until="commit", timeout=10000)
                            logger.info("send_reply: goto 完成，等待会话列表出现")
                        except Exception as e:
                            # goto 失败不崩溃，降级为继续轮询
                            logger.warning(f"send_reply: goto 失败，降级为轮询等待: {e}")

                        # 轮询等待会话列表出现（最多 15 秒）
                        max_wait = 15
                        poll_interval = 0.5
                        elapsed = 0

                        while elapsed < max_wait:
                            conversation_items = page.locator('[class*="conversation-item"]').all()
                            if len(conversation_items) > 0:
                                logger.info(f"send_reply: ✓ 会话列表已出现，找到 {len(conversation_items)} 项")
                                break

                            time.sleep(poll_interval)
                            elapsed += poll_interval

                        # 超时仍未找到会话列表
                        if len(conversation_items) == 0:
                            result["status"] = "failed"
                            result["detail"] = f"Conversation list not loaded after {max_wait}s"
                            logger.error("send_reply failed: conversation list timeout")
                            return result

                    # 查找匹配 target 的会话项
                    target_found = False
                    for item in conversation_items:
                        try:
                            item_text = item.text_content()
                            if target in item_text:
                                logger.info(f"send_reply: 找到目标会话，点击: '{item_text[:50]}'")
                                item.click()
                                time.sleep(2)
                                target_found = True
                                break
                        except:
                            continue

                    if not target_found:
                        result["status"] = "failed"
                        result["detail"] = f"Target conversation not found: '{target}'"
                        logger.error(f"send_reply failed: target '{target}' not found")
                        return result

                    logger.info("send_reply: ✓ 已进入目标会话")

                # 步骤2: 等待输入框出现
                logger.info("send_reply: 等待输入框出现")
                textarea_selector = 'textarea[placeholder^="请输入消息"]'

                try:
                    textarea = page.locator(textarea_selector).first
                    textarea.wait_for(state="visible", timeout=5000)
                    logger.info("send_reply: ✓ 输入框已出现")
                except Exception as e:
                    result["status"] = "failed"
                    result["detail"] = f"Textarea not visible after 5s: {str(e)}"
                    logger.error(f"send_reply failed: textarea timeout")
                    return result

                # 步骤3: 填充内容（React 受控组件）
                logger.info(f"send_reply: 填充内容 '{content}'")
                try:
                    textarea.fill(content)
                    time.sleep(0.5)

                    # 校验填充成功
                    filled_value = textarea.input_value()
                    if filled_value != content:
                        result["status"] = "failed"
                        result["detail"] = f"Fill failed. Expected: '{content}', Got: '{filled_value}'"
                        logger.error(f"send_reply failed: fill verification failed")
                        return result

                    logger.info("send_reply: ✓ 内容已填充")

                except Exception as e:
                    result["status"] = "failed"
                    result["detail"] = f"Failed to fill content: {str(e)}"
                    logger.error(f"send_reply failed to fill: {e}")
                    return result

                # 步骤4: 点击"发送"按钮（优先）
                logger.info("send_reply: 点击发送按钮")
                try:
                    # 发送按钮文字是"发 送"（中间有空格），get_by_text 会处理
                    send_button = page.get_by_text("发送", exact=False).first
                    send_button.click()
                    result["method"] = "click"
                    logger.info("send_reply: ✓ 已点击发送按钮")
                    time.sleep(1.5)

                except Exception as e:
                    # 备选：合成回车
                    logger.warning(f"send_reply: 点击失败，尝试回车: {e}")
                    try:
                        textarea.press("Enter")
                        result["method"] = "Enter"
                        logger.info("send_reply: ✓ 已按 Enter")
                        time.sleep(1.5)
                    except Exception as e2:
                        result["status"] = "failed"
                        result["detail"] = f"Failed to send (click and Enter both failed): {str(e2)}"
                        logger.error(f"send_reply failed to send: {e2}")
                        return result

                # 步骤5: 硬校验 - 检查"自己发出的消息气泡"
                logger.info("send_reply: 硬校验 - 查找自己发出的消息")

                # 证据1（辅助）: textarea 是否清空
                try:
                    after_value = textarea.input_value()
                    textarea_cleared = (after_value == "" or after_value is None)
                    result["evidence"]["textarea_cleared"] = textarea_cleared
                    logger.info(f"send_reply: textarea_cleared = {textarea_cleared}")
                except:
                    result["evidence"]["textarea_cleared"] = False

                # 证据2（硬证据）: 最后一条"自己发的消息"包含发送内容
                try:
                    time.sleep(1)  # 等待消息渲染

                    # 自己发的消息: class 含 message-text-right
                    my_messages = page.locator('[class*="message-text-right"]').all()
                    logger.info(f"send_reply: 找到 {len(my_messages)} 条自己的消息")

                    if len(my_messages) > 0:
                        # 取最后一条
                        last_my_message = my_messages[-1]
                        message_text = last_my_message.text_content()

                        logger.info(f"send_reply: 最后一条自己的消息: '{message_text}'")

                        if content in message_text:
                            result["evidence"]["message_bubble_found"] = True
                            result["evidence"]["message_text"] = message_text
                            result["status"] = "sent"
                            result["detail"] = "Message sent successfully (hard evidence: found in self bubble)"
                            logger.info("send_reply: ✅ 硬证据确认 - 发送成功")
                        else:
                            # 找到自己的消息但内容不匹配
                            result["evidence"]["message_bubble_found"] = False
                            result["evidence"]["last_message_text"] = message_text

                            if textarea_cleared:
                                result["status"] = "uncertain"
                                result["detail"] = f"Textarea cleared but message not found. Last message: '{message_text}'"
                                logger.warning("send_reply: ⚠️  textarea清空但未找到消息气泡")
                            else:
                                result["status"] = "failed"
                                result["detail"] = "Message not found and textarea not cleared"
                                logger.error("send_reply: ✗ 未找到消息且textarea未清空")
                    else:
                        # 没找到任何自己的消息
                        result["evidence"]["message_bubble_found"] = False

                        if textarea_cleared:
                            result["status"] = "uncertain"
                            result["detail"] = "Textarea cleared but no self messages found"
                            logger.warning("send_reply: ⚠️  textarea清空但没有自己的消息")
                        else:
                            result["status"] = "failed"
                            result["detail"] = "No self messages found and textarea not cleared"
                            logger.error("send_reply: ✗ 没有自己的消息且textarea未清空")

                except Exception as e:
                    logger.warning(f"send_reply: 消息气泡验证失败: {e}")
                    result["evidence"]["message_bubble_found"] = False

                    # 如果校验出错，退化到 textarea 清空判断
                    if result["evidence"].get("textarea_cleared"):
                        result["status"] = "uncertain"
                        result["detail"] = f"Verification error but textarea cleared: {str(e)}"
                    else:
                        result["status"] = "failed"
                        result["detail"] = f"Verification error and textarea not cleared: {str(e)}"

                # 截图存档
                try:
                    from pathlib import Path
                    import datetime

                    screenshot_dir = Path("logs/send_reply_screenshots")
                    screenshot_dir.mkdir(parents=True, exist_ok=True)

                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    screenshot_path = screenshot_dir / f"send_{conversation_id}_{timestamp}.png"

                    page.screenshot(path=str(screenshot_path), full_page=True)
                    result["evidence"]["screenshot"] = str(screenshot_path)
                    logger.info(f"send_reply: 截图已保存: {screenshot_path}")

                except Exception as e:
                    logger.warning(f"send_reply: 截图失败: {e}")

                return result

            except Exception as e:
                logger.exception("send_reply unexpected error")
                result["status"] = "failed"
                result["detail"] = f"Unexpected error: {str(e)}"
                return result

        # 在 browser_worker 线程内执行
        return self._run_with_page("send_reply", action)

    def list_item(
        self,
        item: Dict[str, Any],
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not approval_id:
            return {"status": "approval_required", "action": "list_item"}
        return {"status": "not_implemented", "reason": "selectors_need_confirmation"}

    def delist_item(
        self,
        item_id: str,
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not approval_id:
            return {"status": "approval_required", "action": "delist_item"}
        return {"status": "not_implemented", "reason": "selectors_need_confirmation"}

    def ship_order(
        self,
        order_id: str,
        shipment: Dict[str, Any],
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not approval_id:
            return {"status": "approval_required", "action": "ship_order"}
        return {"status": "not_implemented", "reason": "selectors_need_confirmation"}

    def inspect_publish_form(self) -> Dict[str, Any]:
        """Open the real publish flow and collect read-only DOM evidence."""
        if not self.storage_state_path.exists():
            raise PlatformNotLoggedInError("goofish login state not found; please login first")

        def action(page):
            publish_bundle = self._open_publish_page_from_home(page)
            publish_page = publish_bundle["page"]
            publish_entry = publish_bundle["publish_entry"]
            self._pause_if_challenge(publish_page)
            login_state = self._detect_login_state(publish_page)
            if not login_state.get("logged_in"):
                raise PlatformNotLoggedInError(
                    f"please scan QR code and login to Goofish first; detail={login_state.get('detail')}"
                )

            inspection = self._wait_for_publish_form(publish_page, publish_entry)
            screenshot_path = self._capture_step_screenshot(publish_page, "publish-form-inspect")
            self._emit_event(
                "browser",
                "inspect_publish_form",
                "success",
                screenshot_path=screenshot_path,
                detail=f"real Goofish publish form opened: {publish_page.url}",
                data_source="goofish",
            )
            return {
                "status": "success",
                "source": "goofish",
                "url": str(publish_page.url or ""),
                "screenshot_path": screenshot_path,
                "publish_entry": inspection["publish_entry"],
                "controls": inspection["controls"],
                "body_text_sample": inspection["body_text_sample"],
            }

        return self._run_with_page("inspect_publish_form", action)

    def _run_with_page(self, action_name: str, action: Callable[[Any], T]) -> T:
        """Execute action on dedicated browser worker thread."""
        worker = get_browser_worker()

        def execute_on_worker() -> T:
            last_error: Optional[BaseException] = None
            started_at = time.time()
            self._emit_event(
                "browser",
                action_name,
                "start",
                detail="opening real browser",
                data_source="goofish",
            )
            for attempt in range(1, 3):
                try:
                    def wrapped(page):
                        try:
                            result = action(page)
                            self._emit_event(
                                "browser",
                                action_name,
                                "success",
                                duration_ms=(time.time() - started_at) * 1000,
                                detail=f"{action_name} completed",
                                data_source="goofish",
                            )
                            return result
                        except (NotImplementedError, PlatformNotReadyError) as exc:
                            raise exc
                        except PlatformNotLoggedInError as exc:
                            raise exc
                        except Exception as exc:
                            screenshot_path = self.screenshot_dir / f"{action_name}-{attempt}.png"
                            try:
                                page.screenshot(path=str(screenshot_path), full_page=True)
                            except Exception:
                                logger.exception("failed to capture screenshot")
                            logger.exception(
                                "goofish action failed: action=%s attempt=%s screenshot=%s",
                                action_name,
                                attempt,
                                screenshot_path,
                            )
                            self._emit_event(
                                "browser",
                                action_name,
                                "failure",
                                duration_ms=(time.time() - started_at) * 1000,
                                screenshot_path=str(screenshot_path),
                                detail=f"{action_name} failed on attempt {attempt}",
                                error=str(exc),
                                data_source="goofish",
                                metadata={"attempt": attempt},
                            )
                            raise

                    return self.browser_manager.with_page(action_name, wrapped)
                except (NotImplementedError, PlatformNotReadyError) as exc:
                    last_error = exc
                    screenshot_path = self.screenshot_dir / f"{action_name}-not-ready-{attempt}.png"
                    self._emit_event(
                        "browser",
                        action_name,
                        "failure",
                        duration_ms=(time.time() - started_at) * 1000,
                        screenshot_path=str(screenshot_path),
                        detail="real page selectors are not confirmed yet",
                        error=str(exc),
                        data_source="goofish",
                    )
                    raise
                except PlatformNotLoggedInError as exc:
                    last_error = exc
                    screenshot_path = self.screenshot_dir / f"{action_name}-not-logged-in-{attempt}.png"
                    self._emit_event(
                        "browser",
                        action_name,
                        "failure",
                        duration_ms=(time.time() - started_at) * 1000,
                        screenshot_path=str(screenshot_path),
                        detail="local Goofish login is required",
                        error=str(exc),
                        data_source="goofish",
                        metadata={"attempt": attempt, "state": "not_logged_in"},
                    )
                    raise
                except Exception as exc:
                    last_error = exc
                    screenshot_path = self.screenshot_dir / f"{action_name}-{attempt}.png"
                    logger.exception(
                        "goofish action failed before browser callback: action=%s attempt=%s screenshot=%s",
                        action_name,
                        attempt,
                        screenshot_path,
                    )
                    self._emit_event(
                        "browser",
                        action_name,
                        "failure",
                        duration_ms=(time.time() - started_at) * 1000,
                        screenshot_path=str(screenshot_path),
                        detail=f"{action_name} failed on attempt {attempt}",
                        error=str(exc),
                        data_source="goofish",
                        metadata={"attempt": attempt},
                    )

                self._human_delay(min_seconds=2.0, max_seconds=5.0)

            raise RuntimeError(f"goofish action failed: {action_name}") from last_error

        # Execute on dedicated browser worker thread
        return worker.execute(execute_on_worker)

    def _pause_if_challenge(self, page) -> None:
        selector = GOOFISH_SELECTORS.get("captcha_or_slider")
        if selector and page.locator(selector).count() > 0:
            logger.warning("Goofish challenge detected; waiting for manual handling")
            page.pause()
            self.browser_manager.save_storage_state()

    def _goto_domcontentloaded_or_body(self, page, url: str, action_name: str) -> None:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return
        except Exception as exc:
            logger.warning(
                "Goofish navigation did not reach domcontentloaded; checking body before failing: action=%s url=%s error=%s",
                action_name,
                url,
                exc,
            )

        page.wait_for_selector("body", timeout=60000)

    def _open_publish_page_from_home(self, page) -> Dict[str, Any]:
        self._goto_domcontentloaded_or_body(page, GOOFISH_URLS["home"], "open_publish")
        page.wait_for_selector("body", timeout=60000)
        self._pause_if_challenge(page)
        if self._looks_logged_out(page):
            raise PlatformNotLoggedInError("please scan QR code and login to Goofish first")

        publish_entry = page.evaluate(
            r"""(labels) => {
                const isVisible = (el) => {
                  if (!el) return false;
                  const rect = el.getBoundingClientRect();
                  const style = getComputedStyle(el);
                  return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' && style.display !== 'none';
                };
                const selectorFor = (el) => {
                  if (!el) return null;
                  if (el.id) return `#${CSS.escape(el.id)}`;
                  const dataAttrs = ['data-testid', 'data-test', 'data-spm-click', 'data-spm-anchor-id'];
                  for (const attr of dataAttrs) {
                    const value = el.getAttribute(attr);
                    if (value) return `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(value)}"]`;
                  }
                  const href = el.getAttribute('href');
                  if (href) return `${el.tagName.toLowerCase()}[href="${CSS.escape(href)}"]`;
                  const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                  if (text) return `text=${text.slice(0, 40)}`;
                  return el.tagName.toLowerCase();
                };

                const candidates = Array.from(document.querySelectorAll('a,button,[role="button"],div,span'))
                  .map((el) => {
                    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                    return { el, text };
                  })
                  .filter((item) => item.text && labels.some((label) => item.text.includes(label)))
                  .filter((item) => isVisible(item.el))
                  .sort((a, b) => a.text.length - b.text.length);

                for (const item of candidates) {
                  const target = item.el.closest('a,button,[role="button"]') || item.el;
                  if (!isVisible(target)) continue;
                  const rect = target.getBoundingClientRect();
                  return {
                    found: true,
                    text: item.text,
                    selector: selectorFor(target),
                    href: target.getAttribute('href') || '',
                    tag: target.tagName.toLowerCase(),
                    outerHTML: target.outerHTML.slice(0, 2000),
                    x: rect.x + rect.width / 2,
                    y: rect.y + rect.height / 2,
                  };
                }
                return { found: false };
            }""",
            ["发布闲置", "卖闲置", "发布"],
        )
        if not publish_entry or not publish_entry.get("found"):
            raise PlatformNotReadyError("publish entry was not found on the real Goofish home page")

        existing_pages = set(page.context.pages)
        try:
            with page.context.expect_page(timeout=10000) as popup_info:
                page.mouse.click(float(publish_entry["x"]), float(publish_entry["y"]))
            publish_page = popup_info.value
            publish_page.wait_for_load_state("domcontentloaded", timeout=60000)
            publish_page.wait_for_selector("body", timeout=60000)
        except Exception:
            page.mouse.click(float(publish_entry["x"]), float(publish_entry["y"]))
            self._human_delay(min_seconds=2.0, max_seconds=4.0)
            publish_page = page
            for candidate in reversed(page.context.pages):
                if candidate not in existing_pages and not candidate.is_closed():
                    publish_page = candidate
                    break
            publish_page.wait_for_selector("body", timeout=60000)

        try:
            publish_page.wait_for_load_state("domcontentloaded", timeout=60000)
        except Exception:
            pass
        return {"page": publish_page, "publish_entry": publish_entry}

    def _wait_for_publish_form(self, page, publish_entry: Dict[str, Any]) -> Dict[str, Any]:
        page.wait_for_selector("body", timeout=60000)
        deadline = time.time() + 60
        last_payload: Dict[str, Any] = {}
        while time.time() < deadline:
            payload = page.evaluate(
                r"""() => {
                    const isVisible = (el) => {
                      if (!el) return false;
                      const rect = el.getBoundingClientRect();
                      const style = getComputedStyle(el);
                      return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const describe = (el, name) => {
                      if (!el || !isVisible(el)) return null;
                      const selectorParts = [];
                      if (el.id) {
                        selectorParts.push(`#${CSS.escape(el.id)}`);
                      } else {
                        const attrs = ['name', 'placeholder', 'type', 'accept', 'role', 'data-testid', 'data-test', 'class'];
                        for (const attr of attrs) {
                          const value = el.getAttribute(attr);
                          if (value) {
                            selectorParts.push(`${el.tagName.toLowerCase()}[${attr}="${CSS.escape(value)}"]`);
                            break;
                          }
                        }
                      }
                      const selector = selectorParts[0] || el.tagName.toLowerCase();
                      return {
                        name,
                        selector,
                        tag: el.tagName.toLowerCase(),
                        text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim(),
                        placeholder: el.getAttribute('placeholder'),
                        type: el.getAttribute('type'),
                        href: el.getAttribute('href'),
                        outerHTML: el.outerHTML.slice(0, 2000),
                      };
                    };
                    const visible = (list) => list.find((el) => isVisible(el)) || null;
                    const title = visible(Array.from(document.querySelectorAll('input')).filter((el) => {
                      const joined = `${el.getAttribute('placeholder') || ''} ${el.getAttribute('name') || ''} ${el.getAttribute('aria-label') || ''}`.toLowerCase();
                      return /标题|title|宝贝名称|闲置名称/.test(joined);
                    }));
                    const description = visible(Array.from(document.querySelectorAll('textarea,[contenteditable="true"]')).filter((el) => {
                      const joined = `${el.getAttribute('placeholder') || ''} ${el.getAttribute('aria-label') || ''} ${(el.innerText || el.textContent || '')}`.toLowerCase();
                      return /描述|详情|成色|补充/.test(joined);
                    }));
                    const price = visible(Array.from(document.querySelectorAll('input')).filter((el) => {
                      const joined = `${el.getAttribute('placeholder') || ''} ${el.getAttribute('name') || ''} ${el.getAttribute('aria-label') || ''}`.toLowerCase();
                      return /价格|售价|金额|¥|￥|price/.test(joined);
                    }));
                    const category = visible(Array.from(document.querySelectorAll('button,[role="button"],div,span')).filter((el) => {
                      const text = `${el.innerText || el.textContent || ''}`.replace(/\s+/g, ' ').trim();
                      return /分类|类目/.test(text);
                    }));
                    const upload = visible(Array.from(document.querySelectorAll('input[type="file"],button,[role="button"],div,span,label')).filter((el) => {
                      const text = `${el.innerText || el.textContent || ''}`.replace(/\s+/g, ' ').trim();
                      const accept = el.getAttribute('accept') || '';
                      return /上传|图片|相册|拍照/.test(text) || /image/.test(accept);
                    }));
                    const submit = visible(Array.from(document.querySelectorAll('button,[role="button"],div,span')).filter((el) => {
                      const text = `${el.innerText || el.textContent || ''}`.replace(/\s+/g, ' ').trim();
                      return /发布|确认发布|立即发布|提交/.test(text);
                    }));
                    const controls = {
                      title: describe(title, 'title'),
                      description: describe(description, 'description'),
                      price: describe(price, 'price'),
                      category: describe(category, 'category'),
                      upload: describe(upload, 'upload'),
                      submit: describe(submit, 'submit'),
                    };
                    return {
                      url: location.href,
                      title: document.title,
                      bodyTextSample: (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 1200),
                      controls,
                    };
                }"""
            )
            last_payload = payload
            controls = payload.get("controls") or {}
            if all(controls.get(key) for key in ("title", "description", "price", "category", "upload", "submit")):
                return {
                    "publish_entry": publish_entry,
                    "controls": controls,
                    "body_text_sample": payload.get("bodyTextSample", ""),
                }
            time.sleep(1)
        raise PlatformNotReadyError(
            "publish form controls did not all appear within 60s; "
            f"url={page.url} body_text_sample={last_payload.get('bodyTextSample', '')!r}"
        )

    def _open_personal_page_from_home(self, page):
        self._goto_domcontentloaded_or_body(page, GOOFISH_URLS["home"], "open_personal")
        page.wait_for_selector(GOOFISH_SELECTORS["seller_personal_entry"], timeout=60000)
        self._pause_if_challenge(page)
        if self._looks_logged_out(page):
            raise PlatformNotLoggedInError("please scan QR code and login to Goofish first")

        existing_pages = set(page.context.pages)
        account = page.locator(GOOFISH_SELECTORS["seller_personal_entry"]).first
        try:
            with page.context.expect_page(timeout=10000) as new_page_info:
                account.click()
            personal_page = new_page_info.value
            personal_page.wait_for_load_state("domcontentloaded", timeout=60000)
            return personal_page
        except Exception:
            account.click()
            self._human_delay(min_seconds=2.0, max_seconds=4.0)
            for candidate in reversed(page.context.pages):
                if candidate not in existing_pages and "/personal" in str(candidate.url):
                    candidate.wait_for_load_state("domcontentloaded", timeout=60000)
                    return candidate
            for candidate in reversed(page.context.pages):
                if "/personal" in str(candidate.url):
                    candidate.wait_for_load_state("domcontentloaded", timeout=60000)
                    return candidate
            if "/personal" in str(page.url):
                return page
            raise PlatformNotReadyError("Goofish account entry did not open the personal seller page")

    def _wait_for_seller_listing_area(self, page) -> Dict[str, Any]:
        page.wait_for_selector("body", timeout=60000)
        deadline = time.time() + 60
        last_text = ""
        while time.time() < deadline:
            try:
                text = page.locator("body").inner_text(timeout=5000)
                last_text = text[:500]
                item_count = page.locator('a[href*="/item?id="], a[href*="item?id="]').count()
                empty_count = page.locator(GOOFISH_SELECTORS["seller_empty_state"]).count()
                if item_count > 0 or empty_count > 0:
                    return {"url": page.url, "text_sample": last_text}
            except Exception:
                pass
            time.sleep(1)
        raise PlatformNotReadyError(
            f"seller listing area did not appear within 60s; url={page.url} text_sample={last_text!r}"
        )

    def _seller_listing_page_is_empty(self, page) -> bool:
        try:
            text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            return False
        return "\u6682\u65e0\u5546\u54c1" in text and "\u5728\u552e" in text

    def _extract_personal_listing_cards(self, page, limit: int) -> List[Dict[str, Any]]:
        cards = page.locator('a[href*="/item?id="], a[href*="item?id="]')
        try:
            card_count = cards.count()
        except Exception:
            return []

        listings: List[Dict[str, Any]] = []
        for idx in range(min(card_count, limit)):
            card = cards.nth(idx)
            try:
                raw_text = card.inner_text(timeout=3000).strip()
                href = card.get_attribute("href", timeout=3000)
            except Exception:
                continue
            if not raw_text or "\u00a5" not in raw_text:
                continue
            item_url = href
            if item_url and item_url.startswith("/"):
                item_url = f"https://www.goofish.com{item_url}"
            price = self._first_price(raw_text)
            listings.append(
                {
                    "item_id": self._extract_listing_id(item_url, raw_text, idx),
                    "title": self._title_from_listing_text(raw_text),
                    "price": price,
                    "view_count": None,
                    "want_count": self._want_count_from_text(raw_text),
                    "published_at": self._published_at_from_text(raw_text),
                    "item_url": item_url,
                    "status": "on_sale",
                    "platform": "goofish",
                    "source_label": "goofish",
                    "raw_text": raw_text,
                }
            )
        return listings

    def _detect_login_state(self, page) -> Dict[str, Any]:
        storage_summary = summarize_storage_state(self.storage_state_path)
        current_url = str(page.url or "")
        logged_out = self._looks_logged_out(page)
        has_complete_state = bool(
            storage_summary.get("valid")
            and storage_summary.get("important_cookie_count", 0) >= 4
            and storage_summary.get("local_storage_item_count", 0) >= 1
            and storage_summary.get("size", 0) >= 20_000
        )

        if logged_out:
            return {
                "logged_in": False,
                "status": "not_logged_in",
                "detail": "Goofish page still shows login flow",
                "url": current_url,
                "storage_state": storage_summary,
            }

        if has_complete_state:
            return {
                "logged_in": True,
                "status": "logged_in",
                "detail": "storage_state contains Goofish/Taobao auth cookies and localStorage",
                "url": current_url,
                "storage_state": storage_summary,
            }

        return {
            "logged_in": False,
            "status": "not_logged_in",
            "detail": "storage_state is not complete enough to treat as logged in",
            "url": current_url,
            "storage_state": storage_summary,
        }

    def _open_login_prompt_if_needed(self, page) -> None:
        if not self._looks_logged_out(page):
            return

        try:
            target = page.evaluate(
                r"""(labels) => {
                    const isVisible = (el) => {
                      const rect = el.getBoundingClientRect();
                      const style = getComputedStyle(el);
                      return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const clickTargetFor = (el) => el.closest('a,button,[role="button"]') || el;
                    const candidates = Array.from(document.querySelectorAll('a,button,[role="button"],div,span'))
                      .map((el) => {
                        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                        const rect = el.getBoundingClientRect();
                        return { el, text, rect };
                      })
                      .filter((item) => item.text && labels.some((label) => item.text === label || item.text.includes(label)))
                      .filter((item) => isVisible(item.el))
                      .sort((a, b) => {
                        const aExact = labels.includes(a.text) ? 0 : 1;
                        const bExact = labels.includes(b.text) ? 0 : 1;
                        if (aExact !== bExact) return aExact - bExact;
                        return (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height);
                      });

                    for (const item of candidates) {
                      const target = clickTargetFor(item.el);
                      if (!target || !isVisible(target)) continue;
                      const rect = target.getBoundingClientRect();
                      return {
                        found: true,
                        text: item.text,
                        tag: target.tagName,
                        className: target.className || '',
                        x: rect.x + rect.width / 2,
                        y: rect.y + rect.height / 2
                      };
                    }
                    return { found: false };
                }""",
                ["\u7acb\u5373\u767b\u5f55", "\u767b\u5f55"],
            )
            if target and target.get("found"):
                page.mouse.click(float(target["x"]), float(target["y"]))
                logger.info("Goofish login prompt opened: %s", target)
                self._human_delay(min_seconds=2.0, max_seconds=4.0)
                return
        except Exception as exc:
            logger.info("Goofish login prompt DOM click failed: %s", exc)

        try:
            body_text = page.locator("body").inner_text(timeout=3000)
            if "\u7acb\u5373\u767b\u5f55" in body_text:
                viewport = page.viewport_size or {"width": 1280, "height": 720}
                page.mouse.click(viewport["width"] * 0.70, viewport["height"] - 36)
                logger.info("Goofish login prompt opened via visible bottom-login fallback")
                self._human_delay(min_seconds=2.0, max_seconds=4.0)
        except Exception as exc:
            logger.info("Goofish login prompt fallback click failed: %s", exc)

    def _minimize_browser_window(self, page) -> None:
        try:
            session = page.context.new_cdp_session(page)
            window = session.send("Browser.getWindowForTarget")
            session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window["windowId"],
                    "bounds": {"windowState": "minimized"},
                },
            )
        except Exception as exc:
            logger.info("Goofish browser window could not be minimized: %s", exc)

    def _looks_logged_out(self, page) -> bool:
        """
        检测是否未登录
        只检查明确的登录墙标志：
        1. URL跳转到登录页
        2. 页面出现扫码/登录弹层文案
        
        不检查"/personal"入口，避免在搜索页误判
        """
        current_url = str(page.url or "").lower()
        
        # 检查1：URL跳转到登录页
        if any(token in current_url for token in ("login.taobao.com", "passport", "login.htm")):
            return True
        
        # 检查2：页面是否有明确的登录墙文案
        try:
            text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            return False
        
        # 明确的登录墙标志
        login_markers = (
            "扫码登录", "请先登录", "立即登录",
            "手机淘宝扫码", "账号登录", "密码登录",
            "登录后才能", "立刻扫码",
        )
        return any(marker in text for marker in login_markers)


    def _wait_for_search_results(self, page, keyword: str) -> Dict[str, Any]:
        page.wait_for_selector("body", timeout=60000)
        card_selector = GOOFISH_SELECTORS["search_result_card"]
        deadline = time.time() + 60
        last_text = ""
        while time.time() < deadline:
            try:
                if self._looks_logged_out(page):
                    # ✅ 检测到未登录，更新真相源
                    from platforms.login_state import mark_not_logged_in
                    mark_not_logged_in("搜索页面显示未登录")
                    raise PlatformNotLoggedInError("please scan QR code and login to Goofish first")
                text = page.locator("body").inner_text(timeout=5000)
                last_text = text[:500]
                card_count = page.locator(card_selector).count()
                if card_count > 0:
                    return {
                        "url": page.url,
                        "keyword": keyword,
                        "count": card_count,
                        "text_sample": last_text,
                    }
                if "暂无相关宝贝" in text or "没有找到相关" in text:
                    return {
                        "url": page.url,
                        "keyword": keyword,
                        "count": 0,
                        "text_sample": last_text,
                    }
            except PlatformNotLoggedInError:
                raise
            except Exception:
                pass
            time.sleep(1)
        raise PlatformNotReadyError(
            f"search result cards did not appear within 60s; keyword={keyword} "
            f"url={page.url} text_sample={last_text!r}"
        )

    def _extract_competitor_card(self, card, keyword: str, idx: int) -> Dict[str, Any]:
        raw_text = card.inner_text(timeout=3000).strip()
        parsed = self._parse_search_card_text(raw_text)
        title = self._extract_text(card, GOOFISH_SELECTORS.get("result_title")) or parsed["title"]
        price_text = self._extract_text(card, GOOFISH_SELECTORS.get("result_price"))
        price = self._format_price(self._first_price(price_text or raw_text) or parsed["price"])
        metric_text = self._extract_text(card, GOOFISH_SELECTORS.get("result_sold_count"))
        if metric_text and "想要" not in metric_text:
            metric_text = None
        want_text = metric_text or parsed["sold_count"] or self._want_text_from_text(raw_text)
        item_url = self._normalize_item_url(self._extract_attr(card, None, "href"))
        summary = self._summary_from_search_card(raw_text, title, price_text, want_text)
        return {
            "title": title,
            "price": price,
            "sold_count": want_text,
            "want_count": want_text,
            "selling_points": summary,
            "summary": summary,
            "item_url": item_url,
            "source": "goofish",
            "platform": "goofish",
            "source_label": "real",
            "keyword": keyword,
            "rank": idx + 1,
            "raw_text": raw_text,
        }

    def _parse_search_card_text(self, raw_text: str) -> Dict[str, Optional[str]]:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        price = next((line for line in lines if "¥" in line or "元" in line), None)
        sold_count = next((line for line in lines if "想要" in line), None)
        ignored = {
            price,
            sold_count,
            "全新",
            "闲置",
            "卖家信用极好",
            "卖家信用优秀",
            "粉丝好评过百",
        }
        title_parts = [line for line in lines if line not in ignored and "**" not in line]
        title = " ".join(title_parts[:2]).strip() or (lines[0] if lines else raw_text[:120])
        return {
            "title": title,
            "price": price,
            "sold_count": sold_count,
        }

    def _listing_page_diagnostic(self, page) -> Dict[str, str]:
        try:
            text = page.locator("body").inner_text(timeout=3000).strip()
        except Exception as exc:
            text = f"<body text unavailable: {exc}>"
        try:
            title = page.title()
        except Exception:
            title = ""
        return {
            "url": str(page.url or ""),
            "title": title,
            "text_sample": text[:500],
        }

    def _extract_text(self, scope, selector: Optional[str]) -> Optional[str]:
        if not selector:
            return None
        try:
            value = scope.locator(selector).inner_text(timeout=2000).strip()
            return value or None
        except Exception:
            return None

    def _extract_attr(self, scope, selector: Optional[str], attr: str) -> Optional[str]:
        try:
            locator = scope.locator(selector) if selector else scope
            value = locator.get_attribute(attr, timeout=2000)
            return value or None
        except Exception:
            return None

    def _normalize_item_url(self, item_url: Optional[str]) -> Optional[str]:
        if not item_url:
            return None
        if item_url.startswith("//"):
            return f"https:{item_url}"
        if item_url.startswith("/"):
            return f"https://www.goofish.com{item_url}"
        return item_url

    def _extract_listing_id(self, item_url: Optional[str], raw_text: str, idx: int) -> str:
        if item_url:
            import re

            match = re.search(r"(?:id|itemId|item_id)=([^&#]+)", item_url)
            if match:
                return match.group(1)
            return item_url.rstrip("/").rsplit("/", 1)[-1] or f"goofish-listing-{idx}"
        import hashlib

        return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:24] or f"goofish-listing-{idx}"

    def _first_price(self, raw_text: str) -> Optional[str]:
        import re

        normalized = str(raw_text or "").replace("\n", " ").replace(",", "")
        match = re.search(r"(?:¥|￥)\s*([0-9]+)\s*(?:\.\s*([0-9]+))?", normalized)
        if not match:
            return None
        integer = match.group(1)
        decimal = match.group(2)
        return f"{integer}.{decimal}" if decimal else integer

    def _format_price(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.startswith(("¥", "￥")):
            return text.replace("￥", "¥")
        numeric = self._first_price(f"¥{text}") if any(ch.isdigit() for ch in text) else None
        return f"¥{numeric}" if numeric else text

    def _want_count_from_text(self, raw_text: str) -> Optional[str]:
        import re

        match = re.search(r"([0-9]+)\s*人想要", raw_text)
        if not match:
            return None
        return match.group(1)

    def _want_text_from_text(self, raw_text: str) -> Optional[str]:
        value = self._want_count_from_text(raw_text)
        return f"{value}人想要" if value else None

    def _summary_from_search_card(
        self,
        raw_text: str,
        title: Optional[str],
        price_text: Optional[str],
        want_text: Optional[str],
    ) -> str:
        ignored = {
            title,
            price_text,
            want_text,
            "全新",
            "闲置",
            "卖家信用极好",
            "卖家信用优秀",
            "粉丝好评过百",
        }
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        parts: List[str] = []
        for line in lines:
            if line in ignored or line.startswith(("¥", "￥")):
                continue
            if line.isdigit() or (line.startswith(".") and line[1:].isdigit()):
                continue
            if "人想要" in line or "卖家信用" in line:
                continue
            if title and line in title:
                continue
            parts.append(line)
            if len(" ".join(parts)) >= 100:
                break
        return (" ".join(parts).strip() or (title or raw_text[:120]))[:160]

    def _published_at_from_text(self, raw_text: str) -> Optional[str]:
        for marker in ("一周内发布", "72小时内发布", "48小时内发布", "24小时内发布", "刚刚发布"):
            if marker in raw_text:
                return marker
        return None

    def _title_from_listing_text(self, raw_text: str) -> str:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        title_lines = []
        for line in lines:
            if line in {"包邮", "全新", "几乎全新"}:
                continue
            if line.startswith(("¥", "￥")) or "人想要" in line or "卖家信用" in line:
                continue
            title_lines.append(line)
            if len(" ".join(title_lines)) >= 80:
                break
        return " ".join(title_lines).strip()[:160] or raw_text[:120]

    def _human_delay(self, min_seconds: float = 0.6, max_seconds: float = 2.2) -> None:
        time.sleep(random.uniform(min_seconds, max_seconds))

    def _capture_step_screenshot(self, page, label: str) -> Optional[str]:
        target = self.screenshot_dir / f"{label}-{int(time.time() * 1000)}.png"
        try:
            page.screenshot(path=str(target), full_page=True)
            return str(target)
        except Exception:
            logger.exception("failed to capture step screenshot: %s", label)
            return None

    def _emit_event(
        self,
        component: str,
        action_name: str,
        status: str,
        *,
        duration_ms: Optional[float] = None,
        screenshot_path: Optional[str] = None,
        detail: Optional[str] = None,
        error: Optional[str] = None,
        data_source: str = "goofish",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.event_bus.emit(
            component=component,
            action_name=action_name,
            status=status,
            duration_ms=duration_ms,
            screenshot_path=screenshot_path,
            data_source=data_source,
            detail=detail,
            error=error,
            metadata=metadata,
        )
