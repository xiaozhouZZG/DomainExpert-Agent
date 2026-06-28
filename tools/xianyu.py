"""Xianyu / Goofish operation tools."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from core.xianyu_service import (
    list_listings,
    persist_competitor_observations,
    persist_listing_snapshots,
    sync_messages_from_platform,
)
from database.connection import get_db_connection
from platforms.base import PlatformNotLoggedInError, PlatformNotReadyError
from .registry import register_tool

logger = logging.getLogger(__name__)


def _playwright_not_ready_detail(exc: Exception) -> Optional[str]:
    message = str(exc)
    if "Executable doesn't exist" in message or "playwright install" in message.lower():
        return "Playwright browser is not installed; please run `playwright install`."
    lowered = message.lower()
    if any(
        token in lowered
        for token in (
            "targetclosederror",
            "target page, context or browser has been closed",
            "connection closed while reading from the driver",
            "browser process exited",
            "user data directory is already in use",
            "singletonlock",
            "singletonsocket",
            "singletoncookie",
            "exitcode=21",
            "browser profile is already in use",
            "profile is occupied",
            "failed to start goofish browser",
        )
    ):
        return message
    return None


def _get_platform():
    platform_mode = os.getenv("LISTING_PLATFORM", "goofish").lower()
    if platform_mode != "goofish":
        raise RuntimeError(f"LISTING_PLATFORM must be goofish, got {platform_mode}")

    from platforms.goofish_playwright import GoofishPlaywrightPlatform

    return GoofishPlaywrightPlatform()


def _create_platform_approval(
    workflow_type: str,
    title: str,
    content: str,
    order_id: Optional[str] = None,
) -> str:
    current_thread = threading.current_thread()
    customer_id = getattr(current_thread, "current_customer_id", None)
    session_id = getattr(current_thread, "session_id", None)
    trace_id = getattr(current_thread, "trace_id", None)

    approval_id = str(uuid.uuid4())
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO approvals
            (approval_id, workflow_type, customer_id, order_id, session_id, trace_id,
             title, content, amount, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                workflow_type,
                customer_id,
                order_id,
                session_id,
                trace_id,
                title,
                content,
                0.0,
                "pending",
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        return approval_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class ReadXianyuMessagesInput(BaseModel):
    limit: int = Field(20, description="读取消息数量上限")


@register_tool("read_xianyu_messages")
@tool(args_schema=ReadXianyuMessagesInput)
def read_xianyu_messages(limit: int = 20) -> str:
    """读取闲鱼买家消息，只读。"""
    try:
        result = sync_messages_from_platform(_get_platform(), limit=limit)
        return json.dumps(result, ensure_ascii=False)
    except PlatformNotLoggedInError as exc:
        return json.dumps(
            {
                "status": "not_logged_in",
                "detail": str(exc),
                "messages": [],
                "conversations": [],
                "ingest": {"inserted": 0, "updated_conversations": 0},
            },
            ensure_ascii=False,
        )
    except PlatformNotReadyError as exc:
        return json.dumps(
            {
                "status": "not_ready",
                "detail": str(exc),
                "messages": [],
                "conversations": [],
                "ingest": {"inserted": 0, "updated_conversations": 0},
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("read_xianyu_messages failed")
        not_ready_detail = _playwright_not_ready_detail(exc)
        return json.dumps(
            {
                "status": "not_ready" if not_ready_detail else "error",
                "detail": not_ready_detail or str(exc),
                "messages": [],
                "conversations": [],
                "ingest": {"inserted": 0, "updated_conversations": 0},
            },
            ensure_ascii=False,
        )


class FetchXianyuCompetitorsInput(BaseModel):
    keyword: str = Field(..., description="竞品关键词")
    limit: int = Field(20, description="抓取结果数量上限")


@register_tool("fetch_xianyu_competitors")
@tool(args_schema=FetchXianyuCompetitorsInput)
def fetch_xianyu_competitors(keyword: str, limit: int = 20) -> str:
    """抓取公开竞品信息，只读。"""
    try:
        results = _get_platform().fetch_competitor(keyword=keyword, limit=limit)
        persist = persist_competitor_observations(results)
        return json.dumps(
            {"status": "success", "results": results, "persist": persist},
            ensure_ascii=False,
        )
    except PlatformNotLoggedInError as exc:
        return json.dumps(
            {
                "status": "not_logged_in",
                "detail": str(exc),
                "results": [],
                "persist": {"inserted": 0},
            },
            ensure_ascii=False,
        )
    except PlatformNotReadyError as exc:
        return json.dumps(
            {
                "status": "not_ready",
                "detail": str(exc),
                "results": [],
                "persist": {"inserted": 0},
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("fetch_xianyu_competitors failed: keyword=%s", keyword)
        not_ready_detail = _playwright_not_ready_detail(exc)
        return json.dumps(
            {
                "status": "not_ready" if not_ready_detail else "error",
                "detail": not_ready_detail or str(exc),
                "results": [],
                "persist": {"inserted": 0},
            },
            ensure_ascii=False,
        )


class ReadXianyuListingsInput(BaseModel):
    limit: int = Field(50, description="读取在售商品数量上限")


@register_tool("read_xianyu_listings")
@tool(args_schema=ReadXianyuListingsInput)
def read_xianyu_listings(limit: int = 50) -> str:
    """读取闲鱼账号真实在售商品，只读。"""
    try:
        listings = _get_platform().read_listings(limit=limit)
        persist = persist_listing_snapshots(listings)
        return json.dumps(
            {
                "status": "success",
                "listings": list_listings(limit=limit),
                "raw_listings": listings,
                "persist": persist,
            },
            ensure_ascii=False,
        )
    except PlatformNotLoggedInError as exc:
        return json.dumps(
            {
                "status": "not_logged_in",
                "detail": str(exc),
                "listings": [],
                "persist": {"upserted": 0},
            },
            ensure_ascii=False,
        )
    except PlatformNotReadyError as exc:
        return json.dumps(
            {
                "status": "not_ready",
                "detail": str(exc),
                "listings": [],
                "persist": {"upserted": 0},
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("read_xianyu_listings failed")
        not_ready_detail = _playwright_not_ready_detail(exc)
        return json.dumps(
            {
                "status": "not_ready" if not_ready_detail else "error",
                "detail": not_ready_detail or str(exc),
                "listings": [],
                "persist": {"upserted": 0},
            },
            ensure_ascii=False,
        )


class SendXianyuReplyInput(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    content: str = Field(..., description="人工确认后的回复内容")
    approval_id: Optional[str] = Field(None, description="已审批通过的审批单 ID")


@register_tool("send_xianyu_reply")
@tool(args_schema=SendXianyuReplyInput)
def send_xianyu_reply(
    conversation_id: str,
    content: str,
    approval_id: Optional[str] = None,
) -> str:
    """发送闲鱼回复。②子项3: 解耦审批不建单, 直接发(护栏在上层: 自动走白名单+decide_reply, 手动经①鉴权)。
    approval_id 保留但忽略, 兼容 /send-reply 端点与 agent 工具现有签名。"""
    result = _get_platform().send_reply(conversation_id, content, approval_id)
    return json.dumps(result, ensure_ascii=False)


class ListXianyuItemInput(BaseModel):
    item_json: str = Field(..., description="商品上架参数 JSON")
    approval_id: Optional[str] = Field(None, description="已审批通过的审批单 ID")


@register_tool("list_xianyu_item")
@tool(args_schema=ListXianyuItemInput)
def list_xianyu_item(item_json: str, approval_id: Optional[str] = None) -> str:
    """辅助商品上架。没有 approval_id 时仅创建审批单。"""
    if not approval_id:
        new_approval_id = _create_platform_approval(
            workflow_type="xianyu_list_item",
            title="闲鱼商品上架",
            content=item_json,
        )
        return json.dumps(
            {
                "status": "approval_required",
                "approval_id": new_approval_id,
                "action": "list_item",
            },
            ensure_ascii=False,
        )

    from core.xianyu_service import is_approval_approved
    if not is_approval_approved(approval_id, "xianyu_list_item"):
        return json.dumps(
            {"status": "rejected", "action": "list_item",
             "detail": "审批单未通过或动作类型不匹配"},
            ensure_ascii=False,
        )
    item = json.loads(item_json)
    result = _get_platform().list_item(item, approval_id)
    return json.dumps(result, ensure_ascii=False)


class ShipXianyuOrderInput(BaseModel):
    order_id: str = Field(..., description="订单 ID")
    shipment_json: str = Field(..., description="发货信息 JSON")
    approval_id: Optional[str] = Field(None, description="已审批通过的审批单 ID")


@register_tool("ship_xianyu_order")
@tool(args_schema=ShipXianyuOrderInput)
def ship_xianyu_order(
    order_id: str,
    shipment_json: str,
    approval_id: Optional[str] = None,
) -> str:
    """辅助发货。没有 approval_id 时仅创建审批单。"""
    if not approval_id:
        new_approval_id = _create_platform_approval(
            workflow_type="xianyu_ship_order",
            title=f"闲鱼订单发货: {order_id}",
            content=shipment_json,
            order_id=order_id,
        )
        return json.dumps(
            {
                "status": "approval_required",
                "approval_id": new_approval_id,
                "action": "ship_order",
            },
            ensure_ascii=False,
        )

    from core.xianyu_service import is_approval_approved
    if not is_approval_approved(approval_id, "xianyu_ship_order", order_id=order_id):
        return json.dumps(
            {"status": "rejected", "action": "ship_order",
             "detail": "审批单未通过/动作类型不匹配/订单号不匹配"},
            ensure_ascii=False,
        )
    shipment = json.loads(shipment_json)
    result = _get_platform().ship_order(order_id, shipment, approval_id)
    return json.dumps(result, ensure_ascii=False)
