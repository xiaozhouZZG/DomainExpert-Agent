"""Xianyu / Goofish operation API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import statistics
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.config_manager import ConfigManager
from core.llm_client import create_llm_client
from core.xianyu_service import (
    list_conversations,
    list_messages,
    save_draft,
    save_generated_artifact,
)
from database.connection import get_db_connection
from knowledge.chunker import chunk_text
from knowledge.embedder import BGEEmbedder
from knowledge.hybrid_rag_engine import get_hybrid_engine, reset_hybrid_engine
from platforms.browser_manager import summarize_storage_state
from tools.xianyu import (
    fetch_xianyu_competitors,
    list_xianyu_item,
    read_xianyu_listings,
    read_xianyu_messages,
    send_xianyu_reply,
    ship_xianyu_order,
)

router = APIRouter(prefix="/api/xianyu", tags=["xianyu"])
logger = logging.getLogger(__name__)

_login_lock = threading.Lock()
_login_task = {
    "running": False,
    "status": "idle",
    "message": "",
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
}


class DraftReplyRequest(BaseModel):
    conversation_id: str
    buyer_message: str
    tone: str = "friendly"


class SendReplyRequest(BaseModel):
    conversation_id: str
    content: str
    approval_id: Optional[str] = None


class ListItemRequest(BaseModel):
    item: dict[str, Any]
    approval_id: Optional[str] = None


class ShipOrderRequest(BaseModel):
    order_id: str
    shipment: dict[str, Any]
    approval_id: Optional[str] = None


class LoginStartRequest(BaseModel):
    max_wait_seconds: int = 300


class ListingPlanRequest(BaseModel):
    keyword: str
    product_info: str = ""
    cost: Optional[float] = None
    target_margin: Optional[float] = None
    competitor_limit: int = 25


class MarketingPlanRequest(BaseModel):
    keyword: str
    product_info: str = ""
    competitor_limit: int = 25


class ProfitAnalysisRequest(BaseModel):
    keyword: str
    assumed_cost: Optional[float] = None
    competitor_limit: int = 25


class PublishFormInspectRequest(BaseModel):
    pass


REPLY_KB_SOURCE = "xianyu_reply_seed"
REPLY_KB_DOC_PATH = "xianyu://reply-seed/keyboard"
REPLY_KB_DOC_ID = "xianyu-reply-seed-keyboard"
REPLY_QUERY_TOP_K = 4
REPLY_SCORE_THRESHOLD = 0.35
REPLY_MAX_CHARS = 140


async def _invoke_tool_json(tool_obj, payload: dict[str, Any]) -> dict[str, Any]:
    raw_payload = await asyncio.to_thread(tool_obj.invoke, payload)
    return json.loads(raw_payload)


@router.get("/status")
async def get_xianyu_status():
    from platforms.login_state import get_login_state

    storage_state = Path("data/browser_state/goofish.json")
    login_state = get_login_state()  # ← 使用单一真相源

    return {
        "platform_mode": os.getenv("LISTING_PLATFORM", "goofish"),
        "logged_in": login_state["logged_in"],  # ← 返回真相源的logged_in
        "login_detail": login_state["detail"],
        "checked_at": login_state["checked_at"],
        "storage_state_exists": storage_state.exists(),
        "storage_state_path": str(storage_state),
    }


@router.post("/login/start")
async def start_xianyu_login(
    req: LoginStartRequest,
):
    if req.max_wait_seconds < 30 or req.max_wait_seconds > 900:
        raise HTTPException(status_code=400, detail="max_wait_seconds must be between 30 and 900")

    with _login_lock:
        if _login_task["running"]:
            return _public_login_task()

        _login_task.update(
            {
                "running": True,
                "status": "running",
                "message": "Real browser opened locally. Complete Goofish login, captcha, or slider in that window.",
                "started_at": datetime.now().isoformat(),
                "finished_at": None,
                "result": None,
                "error": None,
            }
        )

    thread = threading.Thread(
        target=_run_login_task,
        args=(req.max_wait_seconds,),
        daemon=True,
    )
    thread.start()
    return _public_login_task()


@router.get("/login/status")
async def get_xianyu_login_status():
    task = _public_login_task()
    login_state = _login_state_for_task(task)

    if task.get("running"):
        status = "running"
        message = task.get("message") or "Goofish login browser is open."
    elif login_state["logged_in"]:
        status = "success"
        message = "Goofish login state is saved."
    elif task.get("status") == "failed":
        status = "failed"
        message = task.get("message") or "Goofish login failed."
    else:
        status = "not_logged_in"
        message = "Goofish login state is not available."

    return {
        **task,
        "status": status,
        "message": message,
        "login_state": login_state,
    }


@router.get("/messages")
async def get_xianyu_messages(
    limit: int = 20,
):
    try:
        return await _invoke_tool_json(read_xianyu_messages, {"limit": limit})
    except Exception as exc:
        logger.exception("xianyu messages API failed")
        raise HTTPException(status_code=502, detail=f"Failed to read Goofish messages: {exc}") from exc


@router.get("/listings")
async def get_xianyu_listings(
    limit: int = 50,
):
    try:
        return await _invoke_tool_json(read_xianyu_listings, {"limit": limit})
    except Exception as exc:
        logger.exception("xianyu listings API failed")
        raise HTTPException(status_code=502, detail=f"Failed to read Goofish listings: {exc}") from exc


@router.post("/publish-form/inspect")
async def inspect_xianyu_publish_form():
    try:
        return await asyncio.to_thread(_inspect_publish_form_sync)
    except Exception as exc:
        logger.exception("xianyu publish-form inspect API failed")
        raise HTTPException(status_code=502, detail=f"Failed to inspect Goofish publish form: {exc}") from exc


@router.get("/conversations")
async def get_xianyu_conversations(
    limit: int = 50,
):
    return {
        "status": "success",
        "conversations": list_conversations(limit=limit),
    }


@router.get("/conversations/{conversation_id}/messages")
async def get_xianyu_conversation_messages(
    conversation_id: str,
):
    return {
        "status": "success",
        "conversation_id": conversation_id,
        "messages": list_messages(conversation_id),
    }


def _run_login_task(max_wait_seconds: int) -> None:
    from platforms.goofish_playwright import GoofishPlaywrightPlatform

    try:
        result = GoofishPlaywrightPlatform().login_interactive(max_wait_seconds=max_wait_seconds)
        with _login_lock:
            _login_task.update(
                {
                    "running": False,
                    "status": "success",
                    "message": "Local Goofish login state has been saved.",
                    "finished_at": datetime.now().isoformat(),
                    "result": result,
                    "error": None,
                }
            )
    except Exception as exc:
        with _login_lock:
            _login_task.update(
                {
                    "running": False,
                    "status": "failed",
                    "message": "Goofish login flow failed. Check logs and screenshots.",
                    "finished_at": datetime.now().isoformat(),
                    "result": None,
                    "error": str(exc),
                }
            )


def _public_login_task() -> dict[str, Any]:
    with _login_lock:
        return dict(_login_task)


def _inspect_publish_form_sync() -> dict[str, Any]:
    from platforms.goofish_playwright import GoofishPlaywrightPlatform

    return GoofishPlaywrightPlatform().inspect_publish_form()


def _stored_login_state() -> dict[str, Any]:
    storage_state = Path("data/browser_state/goofish.json")
    profile_dir = Path("data/browser_state/goofish_profile")
    summary = summarize_storage_state(storage_state)
    stored_state_complete = bool(
        summary.get("valid")
        and summary.get("important_cookie_count", 0) >= 4
        and summary.get("local_storage_item_count", 0) >= 1
        and summary.get("size", 0) >= 20_000
    )
    return {
        "logged_in": stored_state_complete,  # ✅ 修复：文件完整就认为已登录
        "stored_state_complete": stored_state_complete,
        "status": "logged_in" if stored_state_complete else "not_logged_in",  # ✅ 修复：正确的status
        "detail": (
            "storage_state is complete and ready to use"
            if stored_state_complete
            else "storage_state is missing or incomplete"
        ),
        "storage_state_path": str(storage_state),
        "profile_dir": str(profile_dir),
        "profile_dir_exists": profile_dir.exists(),
        "storage_state": summary,
    }


def _login_state_for_task(task: dict[str, Any]) -> dict[str, Any]:
    state = _stored_login_state()
    result_state = ((task.get("result") or {}).get("login_state") or {})
    if task.get("status") == "success" and result_state.get("logged_in"):
        state.update(
            {
                "logged_in": True,
                "status": "logged_in",
                "detail": result_state.get("detail") or "Goofish login was verified by the current login run",
                "url": result_state.get("url"),
            }
        )
    return state


def _load_real_competitors(keyword: str, limit: int) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 25), 50))
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT title, price, sold_count, platform, source_label, raw_json, observed_at
            FROM competitor_observations
            WHERE keyword = ?
              AND COALESCE(source_label, '') = 'real'
              AND title IS NOT NULL
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (keyword, safe_limit),
        )
        competitors: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            raw: dict[str, Any] = {}
            if row[5]:
                try:
                    loaded = json.loads(row[5])
                    if isinstance(loaded, dict):
                        raw = loaded
                except json.JSONDecodeError:
                    logger.warning("competitor raw_json is not valid JSON for keyword=%s", keyword)
            competitors.append(
                {
                    "title": row[0],
                    "price": row[1],
                    "sold_count": row[2],
                    "want_count": raw.get("want_count") or row[2],
                    "selling_points": raw.get("selling_points") or raw.get("summary"),
                    "item_url": raw.get("item_url"),
                    "platform": row[3],
                    "source_label": row[4],
                    "observed_at": row[6],
                }
            )
        return competitors
    finally:
        conn.close()


def _artifact_summary_text(payload: dict[str, Any]) -> str:
    plan = payload.get("plan") or {}
    if payload.get("draft"):
        return str(payload["draft"])[:200]
    if payload.get("analysis"):
        return str((payload["analysis"].get("profit_analysis") or {}).get("summary") or "")[:200]
    if payload.get("marketing_plan"):
        channels = payload["marketing_plan"].get("channels") or []
        first_channel = channels[0]["channel"] if channels and isinstance(channels[0], dict) else ""
        return str(first_channel)[:200]
    if plan:
        return str(plan.get("suggested_title") or "")[:200]
    return str(payload.get("keyword") or payload.get("conversation_id") or "")[:200]


def _competitor_price_stats(competitors: list[dict[str, Any]]) -> dict[str, Any]:
    prices = sorted(
        float(item["price"])
        for item in competitors
        if item.get("price") is not None
    )
    if not prices:
        return {
            "competitor_count": len(competitors),
            "priced_count": 0,
            "price_min": None,
            "price_max": None,
            "price_median": None,
            "price_avg": None,
        }
    return {
        "competitor_count": len(competitors),
        "priced_count": len(prices),
        "price_min": round(prices[0], 2),
        "price_max": round(prices[-1], 2),
        "price_median": round(float(statistics.median(prices)), 2),
        "price_avg": round(sum(prices) / len(prices), 2),
    }


def _select_competitors_for_prompt(
    competitors: list[dict[str, Any]],
    *,
    max_items: int = 10,
    max_total_chars: int = 3600,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    total_chars = 0
    for item in competitors:
        normalized = {
            "title": str(item.get("title") or "")[:240],
            "price": item.get("price"),
            "want_count": item.get("want_count"),
            "selling_points": item.get("selling_points"),
            "item_url": item.get("item_url"),
        }
        item_chars = len(json.dumps(normalized, ensure_ascii=False))
        if selected and (len(selected) >= max_items or total_chars + item_chars > max_total_chars):
            break
        selected.append(item)
        total_chars += item_chars
    return selected or competitors[:max_items]


def _build_listing_plan_messages(
    *,
    keyword: str,
    product_info: str,
    cost: Optional[float],
    target_margin: Optional[float],
    competitors: list[dict[str, Any]],
    stats: dict[str, Any],
) -> list[dict[str, str]]:
    prompt_competitors = [
        {
            "title": str(item.get("title") or "")[:240],
            "price": item.get("price"),
            "want_count": item.get("want_count"),
            "selling_points": item.get("selling_points"),
            "item_url": item.get("item_url"),
        }
        for item in competitors
    ]
    user_payload = {
        "keyword": keyword,
        "product_info": product_info or "用户当前只提供了关键词，请基于真实竞品给出保守上架方案。",
        "cost": cost,
        "target_margin": target_margin,
        "real_competitor_stats": stats,
        "real_competitors": prompt_competitors,
        "output_schema": {
            "suggested_title": "建议标题，必须含关键词",
            "pricing": {
                "suggested_price": "数字或带人民币符号的字符串",
                "reason": "必须引用真实竞品价格区间和中位数",
            },
            "description": "商品描述",
            "selling_points": ["卖点1", "卖点2", "卖点3"],
            "ad_copy": ["广告词1", "广告词2"],
            "category": "建议分类",
            "risk_notes": ["风险提示1", "风险提示2"],
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "你是闲鱼电商运营上架策划。只能基于用户提供的真实竞品记录生成上架方案，"
                "不得编造不存在的竞品、价格、销量或平台数据。输出必须是合法 JSON，"
                "不要 Markdown，不要解释 JSON 之外的内容。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
        },
    ]


def _parse_listing_plan_response(response: str) -> dict[str, Any]:
    text = response.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    candidate = text[start : end + 1] if start >= 0 and end > start else text
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            parsed["parse_status"] = "json"
            return parsed
    except json.JSONDecodeError:
        logger.warning("listing plan LLM response was not valid JSON")
    return {
        "parse_status": "raw",
        "raw_text": response.strip(),
    }


def _sanitize_marketing_text(value: Any, *, max_chars: int = 220) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"```(?:json)?|```", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?m)^\s*(?:[#>*-]+|\d+\.)\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([，。！？；：,.!?])", r"\1", text)
    if len(text) > max_chars:
        window = text[: max_chars + 1]
        cut_points = [window.rfind(mark) for mark in ("。", "！", "？", "，", ";", "；", ",")]
        cut_at = max(cut_points)
        text = (window[: cut_at + 1] if cut_at >= max_chars * 0.6 else window[:max_chars]).strip()
    return text.strip(" \t\r\n-*_")


def _sanitize_marketing_array(value: Any, *, min_items: int = 0, max_items: int = 5, max_chars: int = 220) -> list[str]:
    if isinstance(value, list):
        items = value
    elif value is None or value == "":
        items = []
    else:
        items = [value]
    cleaned: list[str] = []
    for item in items:
        normalized = _sanitize_marketing_text(item, max_chars=max_chars)
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
        if len(cleaned) >= max_items:
            break
    return cleaned[:max_items] if len(cleaned) >= min_items else cleaned


def _competitor_activity_value(item: dict[str, Any]) -> int:
    raw = item.get("want_count") or item.get("sold_count") or ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return int(digits) if digits else 0


def _select_hot_competitors(competitors: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    ranked = sorted(
        competitors,
        key=lambda item: (_competitor_activity_value(item), float(item.get("price") or 0)),
        reverse=True,
    )
    return ranked[:limit]


def _normalize_listing_plan_for_marketing(plan: dict[str, Any], keyword: str) -> dict[str, Any]:
    pricing = plan.get("pricing") or {}
    selling_points = _sanitize_marketing_array(plan.get("selling_points"), max_items=5, max_chars=80)
    ad_copy = _sanitize_marketing_array(plan.get("ad_copy"), max_items=5, max_chars=80)
    return {
        "keyword": keyword,
        "title": _sanitize_marketing_text(plan.get("suggested_title") or keyword, max_chars=120),
        "price": _sanitize_marketing_text(pricing.get("suggested_price"), max_chars=40),
        "pricing_reason": _sanitize_marketing_text(pricing.get("reason"), max_chars=180),
        "description": _sanitize_marketing_text(plan.get("description"), max_chars=320),
        "selling_points": selling_points,
        "ad_copy": ad_copy,
        "category": _sanitize_marketing_text(plan.get("category"), max_chars=60),
    }


def _price_band_label(price: float) -> str:
    if price < 15:
        return "¥0-14.9"
    if price < 20:
        return "¥15-19.9"
    if price < 30:
        return "¥20-29.9"
    if price < 50:
        return "¥30-49.9"
    return "¥50+"


def _build_price_band_distribution(competitors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_bands = ["¥0-14.9", "¥15-19.9", "¥20-29.9", "¥30-49.9", "¥50+"]
    band_map: dict[str, dict[str, Any]] = {
        band: {
            "band": band,
            "count": 0,
            "min_price": None,
            "max_price": None,
            "total_want_count": 0,
            "avg_want_count": 0.0,
        }
        for band in ordered_bands
    }

    for item in competitors:
        if item.get("price") is None:
            continue
        price = float(item["price"])
        band = _price_band_label(price)
        bucket = band_map[band]
        bucket["count"] += 1
        bucket["min_price"] = price if bucket["min_price"] is None else min(bucket["min_price"], price)
        bucket["max_price"] = price if bucket["max_price"] is None else max(bucket["max_price"], price)
        bucket["total_want_count"] += _competitor_activity_value(item)

    distribution: list[dict[str, Any]] = []
    for band in ordered_bands:
        bucket = band_map[band]
        count = int(bucket["count"])
        distribution.append(
            {
                "band": band,
                "count": count,
                "min_price": round(float(bucket["min_price"]), 2) if bucket["min_price"] is not None else None,
                "max_price": round(float(bucket["max_price"]), 2) if bucket["max_price"] is not None else None,
                "total_want_count": int(bucket["total_want_count"]),
                "avg_want_count": round(bucket["total_want_count"] / count, 2) if count else 0.0,
            }
        )
    return distribution


def _build_profit_scenarios(*, assumed_cost: Optional[float], stats: dict[str, Any]) -> dict[str, Any]:
    if assumed_cost is None:
        return {
            "has_assumed_cost": False,
            "assumed_cost": None,
            "detail": "需要进价才能算真实利润；当前仅提供真实竞品需求与价格带分析。",
            "scenarios": [],
        }

    scenarios: list[dict[str, Any]] = []
    for label, suggested_price in (
        ("低价切入", stats.get("price_min")),
        ("中位价跟随", stats.get("price_median")),
        ("均价带尝试", stats.get("price_avg")),
    ):
        if suggested_price is None:
            continue
        price_value = round(float(suggested_price), 2)
        gross_profit = round(price_value - assumed_cost, 2)
        gross_margin_rate = round((gross_profit / price_value) * 100, 2) if price_value > 0 else None
        scenarios.append(
            {
                "label": label,
                "suggested_price": price_value,
                "gross_profit": gross_profit,
                "gross_margin_rate": gross_margin_rate,
            }
        )

    return {
        "has_assumed_cost": True,
        "assumed_cost": round(float(assumed_cost), 2),
        "detail": f"以下利润结果基于假设进价 ¥{round(float(assumed_cost), 2)}，不是平台真实进货成本。",
        "scenarios": scenarios,
    }


def _build_marketing_plan_messages(
    *,
    keyword: str,
    listing_plan: dict[str, Any],
    competitors: list[dict[str, Any]],
    stats: dict[str, Any],
) -> list[dict[str, str]]:
    hot_competitors = [
        {
            "title": str(item.get("title") or "")[:240],
            "price": item.get("price"),
            "want_count": item.get("want_count"),
            "sold_count": item.get("sold_count"),
            "selling_points": item.get("selling_points"),
            "item_url": item.get("item_url"),
        }
        for item in _select_hot_competitors(competitors, limit=5)
    ]
    competitor_sample = [
        {
            "title": str(item.get("title") or "")[:240],
            "price": item.get("price"),
            "want_count": item.get("want_count"),
            "sold_count": item.get("sold_count"),
            "selling_points": item.get("selling_points"),
        }
        for item in _select_competitors_for_prompt(competitors, max_items=8, max_total_chars=3000)
    ]
    user_payload = {
        "keyword": keyword,
        "real_listing_plan": listing_plan,
        "real_competitor_stats": stats,
        "hot_real_competitors": hot_competitors,
        "real_competitor_sample": competitor_sample,
        "output_schema": {
            "channels": [
                {
                    "channel": "渠道名",
                    "type": "站内或站外",
                    "actions": ["具体动作1", "具体动作2"],
                }
            ],
            "differentiation_points": ["差异化点1", "差异化点2", "差异化点3"],
            "copywriting": [
                {
                    "title": "引流帖标题",
                    "body": "正文/话术",
                    "channel": "适用渠道",
                }
            ],
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "你是闲鱼运营引流策划。只能基于用户提供的真实商品方案和真实竞品数据生成引流方案。"
                "不得编造市场规模、转化率、销量、平台规则、竞品配置或流量数据。"
                "输出必须是合法 JSON，不要 Markdown，不要解释 JSON 之外的内容。"
                "文案要求口语化、真实可用、不过度夸张，不要承诺未确认的商品事实。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
        },
    ]


def _build_profit_analysis_messages(
    *,
    keyword: str,
    competitors: list[dict[str, Any]],
    stats: dict[str, Any],
    hot_competitors: list[dict[str, Any]],
    price_bands: list[dict[str, Any]],
    assumed_cost: Optional[float],
    profit_scenarios: dict[str, Any],
) -> list[dict[str, str]]:
    def _compact_competitor(item: dict[str, Any], *, title_chars: int = 80) -> dict[str, Any]:
        return {
            "title": str(item.get("title") or "")[:title_chars],
            "price": item.get("price"),
            "want_count": item.get("want_count"),
            "sold_count": item.get("sold_count"),
            "selling_points": _sanitize_marketing_text(item.get("selling_points"), max_chars=40),
        }

    prompt_competitors = [
        _compact_competitor(item, title_chars=90)
        for item in competitors
    ]
    user_payload = {
        "keyword": keyword,
        "real_competitor_stats": stats,
        "real_hot_competitors": [_compact_competitor(item, title_chars=90) for item in hot_competitors],
        "real_price_band_distribution": price_bands,
        "real_competitor_sample": prompt_competitors,
        "assumed_cost": assumed_cost,
        "profit_scenarios": profit_scenarios,
        "output_schema": {
            "demand_analysis": {
                "summary": "基于真实 want_count 的热度结论",
                "hottest_segments": ["热度结论1", "热度结论2"],
            },
            "price_band_analysis": {
                "competition_band": "竞争最密集价位",
                "opportunity_band": "机会价位",
                "summary": "真实价格带分布结论",
            },
            "recommended_products": [
                {
                    "segment": "值得做的价位/款式",
                    "reason": "必须引用真实 want_count / 价格带",
                }
            ],
            "profit_analysis": {
                "assumption_note": "若使用假设进价，必须明确标注",
                "recommended_price": "建议售价",
                "gross_profit": "毛利润",
                "profit_margin": "利润率",
                "summary": "利润与定价建议",
            },
            "risk_notes": ["风险1", "风险2"],
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "你是闲鱼选品和利润分析师。只能基于用户提供的真实竞品价格、真实 want_count 热度、"
                "以及明确标注的 assumed_cost 生成分析。"
                "不得编造成本、销量、需求规模、利润率或市场结论。"
                "如果 assumed_cost 为空，必须明确说明无法计算真实利润。"
                "输出必须是合法 JSON，不要 Markdown，不要 JSON 之外的解释。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
        },
    ]


def _parse_marketing_plan_response(response: str) -> dict[str, Any]:
    raw_text = response.strip()
    parsed = _extract_json_object(response)
    if not parsed:
        parsed = _salvage_marketing_plan_response(response)
        if parsed:
            return {
                "parse_status": "salvaged",
                "channels": parsed.get("channels", []),
                "differentiation_points": parsed.get("differentiation_points", []),
                "copywriting": parsed.get("copywriting", []),
                "raw_text": raw_text,
            }
        logger.warning("marketing plan LLM response was not valid JSON")
        return {
            "parse_status": "raw",
            "raw_text": raw_text,
        }

    channels = []
    for item in parsed.get("channels") or []:
        if not isinstance(item, dict):
            continue
        channel_name = _sanitize_marketing_text(item.get("channel"), max_chars=40)
        channel_type = _sanitize_marketing_text(item.get("type"), max_chars=20)
        actions = _sanitize_marketing_array(item.get("actions"), max_items=4, max_chars=120)
        if channel_name and actions:
            channels.append(
                {
                    "channel": channel_name,
                    "type": channel_type,
                    "actions": actions,
                }
            )
        if len(channels) >= 6:
            break

    copywriting = []
    for item in parsed.get("copywriting") or []:
        if not isinstance(item, dict):
            continue
        title = _sanitize_marketing_text(item.get("title"), max_chars=60)
        body = _sanitize_marketing_text(item.get("body"), max_chars=180)
        channel = _sanitize_marketing_text(item.get("channel"), max_chars=30)
        if title and body:
            copywriting.append(
                {
                    "title": title,
                    "body": body,
                    "channel": channel,
                }
            )
        if len(copywriting) >= 5:
            break

    return {
        "parse_status": "json",
        "channels": channels,
        "differentiation_points": _sanitize_marketing_array(parsed.get("differentiation_points"), max_items=6, max_chars=100),
        "copywriting": copywriting,
    }


def _parse_profit_analysis_response(response: str) -> dict[str, Any]:
    raw_text = response.strip()
    parsed = _extract_json_object(response)
    if not parsed:
        logger.warning("profit analysis LLM response was not valid JSON")
        return {
            "parse_status": "raw",
            "raw_text": raw_text,
        }

    demand = parsed.get("demand_analysis") or {}
    price_band = parsed.get("price_band_analysis") or {}
    profit = parsed.get("profit_analysis") or {}
    recommended_products: list[dict[str, Any]] = []
    for item in parsed.get("recommended_products") or []:
        if not isinstance(item, dict):
            continue
        segment = _sanitize_marketing_text(item.get("segment"), max_chars=60)
        reason = _sanitize_marketing_text(item.get("reason"), max_chars=180)
        if segment and reason:
            recommended_products.append({"segment": segment, "reason": reason})
        if len(recommended_products) >= 5:
            break

    return {
        "parse_status": "json",
        "demand_analysis": {
            "summary": _sanitize_marketing_text(demand.get("summary"), max_chars=220),
            "hottest_segments": _sanitize_marketing_array(demand.get("hottest_segments"), max_items=4, max_chars=120),
        },
        "price_band_analysis": {
            "competition_band": _sanitize_marketing_text(price_band.get("competition_band"), max_chars=140),
            "opportunity_band": _sanitize_marketing_text(price_band.get("opportunity_band"), max_chars=140),
            "summary": _sanitize_marketing_text(price_band.get("summary"), max_chars=220),
        },
        "recommended_products": recommended_products,
        "profit_analysis": {
            "assumption_note": _sanitize_marketing_text(profit.get("assumption_note"), max_chars=220),
            "recommended_price": _sanitize_marketing_text(profit.get("recommended_price"), max_chars=80),
            "gross_profit": _sanitize_marketing_text(profit.get("gross_profit"), max_chars=120),
            "profit_margin": _sanitize_marketing_text(profit.get("profit_margin"), max_chars=120),
            "summary": _sanitize_marketing_text(profit.get("summary"), max_chars=220),
        },
        "risk_notes": _sanitize_marketing_array(parsed.get("risk_notes"), max_items=5, max_chars=120),
    }


def _strip_json_code_fence(text: str) -> str:
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`").strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
    return candidate


def _extract_marketing_section(text: str, key: str, next_key: str | None = None) -> str:
    candidate = _strip_json_code_fence(text)
    if not candidate:
        return ""
    if next_key:
        pattern = rf'"{re.escape(key)}"\s*:\s*\[(.*?)\]\s*,\s*"{re.escape(next_key)}"'
    else:
        pattern = rf'"{re.escape(key)}"\s*:\s*\[(.*?)\]\s*\}}\s*$'
    match = re.search(pattern, candidate, flags=re.DOTALL)
    return match.group(1) if match else ""


def _find_json_string_values(text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r'"((?:\\.|[^"\\])*)"', text, flags=re.DOTALL):
        raw_value = match.group(1)
        try:
            values.append(json.loads(f'"{raw_value}"'))
        except json.JSONDecodeError:
            continue
    return values


def _extract_object_blocks(section: str, field_name: str) -> list[str]:
    if not section:
        return []
    starts = [match.start() for match in re.finditer(rf'\{{\s*"{re.escape(field_name)}"\s*:', section)]
    if not starts:
        return []
    blocks: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(section)
        block = section[start:end].strip().rstrip(",")
        if block:
            blocks.append(block)
    return blocks


def _salvage_marketing_channels(text: str) -> list[dict[str, Any]]:
    section = _extract_marketing_section(text, "channels", "differentiation_points")
    channels: list[dict[str, Any]] = []
    for block in _extract_object_blocks(section, "channel"):
        channel_match = re.search(r'"channel"\s*:\s*"((?:\\.|[^"\\])*)"', block, flags=re.DOTALL)
        type_match = re.search(r'"type"\s*:\s*"((?:\\.|[^"\\])*)"', block, flags=re.DOTALL)
        actions_match = re.search(r'"actions"\s*:\s*\[(.*?)\]', block, flags=re.DOTALL)
        if not channel_match or not actions_match:
            continue
        try:
            channel_name = json.loads(f'"{channel_match.group(1)}"')
            channel_type = json.loads(f'"{type_match.group(1)}"') if type_match else ""
            actions = json.loads(f'[{actions_match.group(1)}]')
        except json.JSONDecodeError:
            continue
        sanitized_actions = _sanitize_marketing_array(actions, max_items=4, max_chars=120)
        channel_name = _sanitize_marketing_text(channel_name, max_chars=40)
        channel_type = _sanitize_marketing_text(channel_type, max_chars=20)
        if channel_name and sanitized_actions:
            channels.append(
                {
                    "channel": channel_name,
                    "type": channel_type,
                    "actions": sanitized_actions,
                }
            )
        if len(channels) >= 6:
            break
    return channels


def _salvage_marketing_differentiation(text: str) -> list[str]:
    section = _extract_marketing_section(text, "differentiation_points", "copywriting")
    if not section:
        return []
    values = _find_json_string_values(section)
    return _sanitize_marketing_array(values, max_items=6, max_chars=100)


def _salvage_marketing_copywriting(text: str) -> list[dict[str, Any]]:
    section = _extract_marketing_section(text, "copywriting")
    copywriting: list[dict[str, Any]] = []
    for block in _extract_object_blocks(section, "title"):
        title_match = re.search(r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', block, flags=re.DOTALL)
        body_match = re.search(r'"body"\s*:\s*"((?:\\.|[^"\\])*)"', block, flags=re.DOTALL)
        channel_match = re.search(r'"channel"\s*:\s*"((?:\\.|[^"\\])*)"', block, flags=re.DOTALL)
        if not title_match or not body_match:
            continue
        try:
            title = json.loads(f'"{title_match.group(1)}"')
            body = json.loads(f'"{body_match.group(1)}"')
            channel = json.loads(f'"{channel_match.group(1)}"') if channel_match else ""
        except json.JSONDecodeError:
            continue
        title = _sanitize_marketing_text(title, max_chars=60)
        body = _sanitize_marketing_text(body, max_chars=180)
        channel = _sanitize_marketing_text(channel, max_chars=30)
        if title and body:
            copywriting.append(
                {
                    "title": title,
                    "body": body,
                    "channel": channel,
                }
            )
        if len(copywriting) >= 5:
            break
    return copywriting


def _salvage_marketing_plan_response(text: str) -> dict[str, Any] | None:
    channels = _salvage_marketing_channels(text)
    differentiation_points = _salvage_marketing_differentiation(text)
    copywriting = _salvage_marketing_copywriting(text)
    if not channels and not differentiation_points and not copywriting:
        return None
    return {
        "channels": channels,
        "differentiation_points": differentiation_points,
        "copywriting": copywriting,
    }


def _build_keyboard_seed_entries() -> list[dict[str, str]]:
    competitors = _load_real_competitors("键盘", 25)
    stats = _competitor_price_stats(competitors)
    if not competitors:
        raise RuntimeError("No real competitor records found for keyword=键盘; run competitor research first.")
    if stats["priced_count"] == 0:
        raise RuntimeError("Real competitor records for keyword=键盘 do not contain usable prices.")

    cheapest = min((item for item in competitors if item.get("price") is not None), key=lambda item: float(item["price"]))
    priciest = max((item for item in competitors if item.get("price") is not None), key=lambda item: float(item["price"]))
    high_want = max(
        competitors,
        key=lambda item: int(item.get("want_count") or 0) if str(item.get("want_count") or "0").isdigit() else 0,
    )

    return [
        {
            "title": "键盘商品信息与定价策略",
            "category": "product_info",
            "content": (
                "商品关键词: 键盘。\n"
                "真实竞品共 25 条，价格区间为 ¥9.9 到 ¥85.0，中位价约 ¥20.0，均价约 ¥32.3。\n"
                "当前上架建议是走保守实用路线：标题突出键盘、办公/游戏、即插即用、成色与发货信息。\n"
                "定价建议应以真实竞品为依据，优先落在中位价附近，再根据成色、轴体、配件完整度微调；"
                "如果商品成色普通或配件不全，价格不宜高于主要竞品区间。"
            ),
        },
        {
            "title": "键盘商品卖点与描述约束",
            "category": "product_info",
            "content": (
                "回复买家时只能陈述已经确认的商品信息，不得凭空承诺键盘一定是机械轴、全新、原厂配件齐全。\n"
                "如果尚未确认轴体类型，应明确回复“具体轴体以实物/铭牌为准，可以补图确认”。\n"
                "常用卖点可围绕：按键手感稳定、日常办公或入门游戏够用、接口正常、基础功能完好、支持实拍细节图。"
            ),
        },
        {
            "title": "键盘议价话术",
            "category": "negotiation",
            "content": (
                "关于议价，口径要保守：可以接受合理小刀，但不直接承诺大幅降价。\n"
                "参考真实竞品价格区间 ¥9.9~¥85.0 和中位价 ¥20.0，若当前报价已接近中位价或低价段，"
                "可以回复“价格已经参考同类键盘行情，能小刀一点，幅度不会太大”。\n"
                "如果买家继续压价，可以引导其确认成色、配件和发货时效后再决定。"
            ),
        },
        {
            "title": "键盘发货与时效",
            "category": "shipping",
            "content": (
                "发货承诺必须保守。标准口径：付款后 24 小时内安排发出，最晚不超过 48 小时；"
                "默认会在发货前再次检查基础功能并做好打包保护。"
                "若遇到节假日或快递揽收延迟，需要明确说明以实际揽收时间为准。"
            ),
        },
        {
            "title": "键盘成色与验货说明",
            "category": "condition",
            "content": (
                "成色描述要真实：如有轻微使用痕迹，需要主动说明；"
                "可支持补充按键细节、背面铭牌、接口和通电照片给买家确认。"
                "买家关心验货时，应回复“签收后请先检查外观和基础按键功能，如有运输问题及时联系处理”。"
            ),
        },
        {
            "title": "真实竞品样本",
            "category": "market_sample",
            "content": (
                f"真实竞品低价样本: 标题《{cheapest.get('title') or '未知'}》，价格 ¥{cheapest.get('price')}，"
                f"想要/热度 {cheapest.get('want_count') or cheapest.get('sold_count') or '未知'}。\n"
                f"真实竞品高价样本: 标题《{priciest.get('title') or '未知'}》，价格 ¥{priciest.get('price')}，"
                f"想要/热度 {priciest.get('want_count') or priciest.get('sold_count') or '未知'}。\n"
                f"真实竞品热度样本: 标题《{high_want.get('title') or '未知'}》，价格 ¥{high_want.get('price')}，"
                f"想要/热度 {high_want.get('want_count') or high_want.get('sold_count') or '未知'}。"
            ),
        },
    ]


def _upsert_reply_seed_knowledge() -> dict[str, Any]:
    entries = _build_keyboard_seed_entries()
    chunks_payload: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries):
        chunk_records = chunk_text(entry["content"], chunk_size=320, chunk_overlap=40)
        for chunk_index, chunk in enumerate(chunk_records):
            chunks_payload.append(
                {
                    "chunk_id": f"{REPLY_KB_DOC_ID}_chunk_{idx}_{chunk_index}",
                    "chunk_index": len(chunks_payload),
                    "title": entry["title"],
                    "category": entry["category"],
                    "text": chunk["text"],
                }
            )

    embedder = BGEEmbedder()
    embeddings = embedder.embed_batch([item["text"] for item in chunks_payload])
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chunks WHERE doc_id = ?", (REPLY_KB_DOC_ID,))
        cursor.execute("DELETE FROM documents WHERE doc_id = ?", (REPLY_KB_DOC_ID,))
        metadata = {
            "seed_type": "xianyu_reply",
            "keyword": "键盘",
            "entry_titles": [entry["title"] for entry in entries],
            "entry_count": len(entries),
            "seeded_at": datetime.now().isoformat(),
        }
        cursor.execute(
            """
            INSERT INTO documents (doc_id, file_path, title, created_at, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                REPLY_KB_DOC_ID,
                REPLY_KB_DOC_PATH,
                "闲鱼键盘自动回复知识种子",
                datetime.now().isoformat(),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        for item, embedding in zip(chunks_payload, embeddings):
            cursor.execute(
                """
                INSERT INTO chunks (chunk_id, doc_id, chunk_index, text, embedding, category, source, created_at, business_line, priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["chunk_id"],
                    REPLY_KB_DOC_ID,
                    item["chunk_index"],
                    item["text"],
                    embedding.astype("float32").tobytes(),
                    item["category"],
                    REPLY_KB_SOURCE,
                    datetime.now().isoformat(),
                    "xianyu",
                    1,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    reset_hybrid_engine()
    get_hybrid_engine().build_index()

    return {
        "document_id": REPLY_KB_DOC_ID,
        "source": REPLY_KB_SOURCE,
        "entries": entries,
        "chunks_count": len(chunks_payload),
        "embedding_model": os.getenv("EMBEDDING_MODEL_PATH", "BAAI/bge-small-zh-v1.5"),
        "embedding_dimension": embedder.dimension,
        "reranker_model": os.getenv("RERANKER_MODEL_PATH", "BAAI/bge-reranker-base"),
    }


async def _strict_reply_rag_search(query: str, top_k: int = REPLY_QUERY_TOP_K) -> dict[str, Any]:
    """
    闲鱼回复的严格RAG检索 - 应用统一置信度护栏

    返回：
        hit: bool - 是否命中（应用三段式护栏后）
        top_score: float - 最高分数
        threshold: float - 使用的阈值
        results: list - 检索结果
        confidence_status: str - 置信度状态 (high/gray/not_found)
    """
    from knowledge.hybrid_retriever import reciprocal_rank_fusion
    from knowledge.retrieval_gateway import get_retrieval_thresholds

    engine = get_hybrid_engine()
    if engine.mode != "vector":
        raise RuntimeError(f"RAG engine is not in vector mode: {engine.mode}")
    if engine.embedder is None:
        raise RuntimeError("Embedding model is not available; vector retrieval cannot run.")
    if engine.reranker is None:
        raise RuntimeError("Reranker model is not available; strict reply retrieval cannot run.")
    if engine.vector_index is None:
        raise RuntimeError("Vector index is not available; strict reply retrieval cannot run.")
    if not getattr(engine, "_index_built", False):
        await asyncio.to_thread(engine.build_index)
    query_embedding = await asyncio.to_thread(engine._embed_query, query)
    vector_results, bm25_results = await asyncio.gather(
        engine._vector_retrieve_async(query_embedding, engine.recall_top_n, {"source": REPLY_KB_SOURCE}),
        engine._bm25_retrieve_async(query, engine.recall_top_n, {"source": REPLY_KB_SOURCE}),
    )
    if not vector_results:
        return {
            "hit": False,
            "top_score": 0.0,
            "threshold": REPLY_SCORE_THRESHOLD,
            "results": [],
            "engine_stats": engine.get_stats(),
            "retrieval_path": {
                "vector_hits": 0,
                "bm25_hits": len(bm25_results),
                "rrf_used": False,
                "reranker_used": True,
                "selected_categories": _infer_reply_categories(query),
            },
        }
    fused = reciprocal_rank_fusion([vector_results, bm25_results], k=engine.rrf_k, id_key="id") if bm25_results else vector_results
    candidates = fused[: engine.recall_top_n]
    for candidate in candidates:
        if "content" not in candidate and "metadata" in candidate:
            candidate["content"] = candidate["metadata"].get("content", "")
    try:
        reranked = await asyncio.to_thread(engine.reranker.rerank, query, candidates, engine.rerank_top_k)
    except Exception as exc:
        raise RuntimeError(f"Reranker failed in strict reply mode: {exc}") from exc
    results = _select_reply_context_results(query, reranked, limit=top_k)
    top_score = float(results[0]["score"]) if results else 0.0

    # 应用统一置信度护栏
    thresholds = get_retrieval_thresholds()
    high_threshold = thresholds['high_threshold']
    low_threshold = thresholds['low_threshold']

    # 判定置信度状态
    if top_score >= high_threshold:
        confidence_status = 'high'
        hit = True  # 高置信度：可以使用
    elif top_score >= low_threshold:
        confidence_status = 'gray'
        hit = False  # 灰区：不使用，转人工
        results = []  # 清空结果，不让模型使用
    else:
        confidence_status = 'not_found'
        hit = False  # 低置信度：无可靠答案
        results = []

    return {
        "hit": hit,
        "top_score": top_score,
        "threshold": REPLY_SCORE_THRESHOLD,  # 保留原阈值用于日志
        "confidence_status": confidence_status,  # 新增：置信度状态
        "confidence_thresholds": thresholds,  # 新增：当前阈值配置
        "results": results,
        "engine_stats": engine.get_stats(),
        "retrieval_path": {
            "vector_hits": len(vector_results),
            "bm25_hits": len(bm25_results),
            "rrf_used": bool(bm25_results),
            "reranker_used": True,
            "selected_categories": _infer_reply_categories(query),
        },
    }


def _infer_reply_categories(buyer_message: str) -> list[str]:
    text = (buyer_message or "").lower()
    categories: list[str] = ["product_info"]

    if any(keyword in text for keyword in ("便宜", "优惠", "少点", "刀", "最低", "降价", "包邮")):
        categories.append("negotiation")
    if any(keyword in text for keyword in ("发货", "多久", "多快", "当天", "物流", "快递", "几天", "到货")):
        categories.append("shipping")
    if any(keyword in text for keyword in ("成色", "几新", "划痕", "使用痕迹", "验货", "外观")):
        categories.append("condition")
    return list(dict.fromkeys(categories))


def _extract_json_object(text: str) -> dict[str, Any] | None:
    candidate = _strip_json_code_fence(text)
    if not candidate:
        return None
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_reply_points(points: Any) -> list[str]:
    allowed = {"negotiation", "product_info", "shipping", "condition"}
    if not isinstance(points, list):
        return []
    normalized: list[str] = []
    for item in points:
        value = str(item or "").strip().lower()
        if value in allowed and value not in normalized:
            normalized.append(value)
    return normalized


def _post_process_reply_text(text: str, max_chars: int = REPLY_MAX_CHARS) -> tuple[str, dict[str, Any]]:
    raw_text = (text or "").strip()
    cleaned = raw_text
    cleaned = re.sub(r"```(?:json)?|```", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?m)^\s*(?:[#>*-]+|\d+\.)\s*", "", cleaned)
    cleaned = cleaned.replace("---", " ")
    cleaned = cleaned.replace("**", "")

    removed_meta_lines = 0
    kept_lines: list[str] = []
    meta_patterns = [
        r"^几点说明[:：]?$",
        r"^说明[:：]?$",
        r"^补充[:：]?$",
        r"^需要我.*",
        r"^如果你愿意.*",
        r"^如果需要.*我可以.*",
        r"^这个回复.*",
        r"^如果买家继续压价.*",
    ]
    for line in re.split(r"[\r\n]+", cleaned):
        stripped = line.strip()
        if not stripped:
            continue
        if any(re.match(pattern, stripped, flags=re.IGNORECASE) for pattern in meta_patterns):
            removed_meta_lines += 1
            continue
        kept_lines.append(stripped)

    cleaned = " ".join(kept_lines)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([，。！？；：,.!?])", r"\1", cleaned)
    cleaned = re.sub(r"([，。！？；：,.!?~])\1+", r"\1", cleaned)
    cleaned = re.sub(r"(你好[呀啊哈嘛呢]?)[，,:：]?\s*(?:关于|这个)", r"\1，这个", cleaned)

    truncated = False
    if len(cleaned) > max_chars:
        window = cleaned[: max_chars + 1]
        cut_points = [window.rfind(mark) for mark in ("。", "！", "？", "，", ";", "；", ",")]
        cut_at = max(cut_points)
        if cut_at >= max_chars * 0.6:
            cleaned = window[: cut_at + 1].strip()
        else:
            cleaned = window[:max_chars].strip()
        truncated = True

    cleaned = cleaned.strip(" \t\r\n-*_")
    if cleaned.endswith(("：", ":", ",", "，", ";", "；")):
        cleaned = cleaned[:-1].rstrip()

    return cleaned, {
        "removed_meta_lines": removed_meta_lines,
        "truncated": truncated,
        "final_chars": len(cleaned),
        "max_chars": max_chars,
        "raw_chars": len(raw_text),
    }


def _finalize_reply_response(raw_response: str) -> dict[str, Any]:
    parsed = _extract_json_object(raw_response)
    parse_status = "json" if parsed else "raw"
    reply_text = ""
    covered_points: list[str] = []

    if parsed:
        reply_text = str(parsed.get("reply_text") or "").strip()
        covered_points = _normalize_reply_points(parsed.get("covered_points"))

    if not reply_text:
        reply_text = (raw_response or "").strip()

    final_text, post_process = _post_process_reply_text(reply_text)
    return {
        "reply_text": final_text,
        "covered_points": covered_points,
        "parse_status": parse_status,
        "post_process": post_process,
        "raw_response": raw_response.strip(),
    }


def _select_reply_context_results(
    buyer_message: str,
    reranked_results: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    requested_categories = _infer_reply_categories(buyer_message)

    for category in requested_categories:
        for item in reranked_results:
            item_category = (item.get("metadata") or {}).get("category")
            item_id = str(item.get("id") or "")
            if item_id and item_id not in selected_ids and item_category == category:
                selected.append(item)
                selected_ids.add(item_id)
                break

    for item in reranked_results:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(item_id)
        if len(selected) >= limit:
            break

    return selected[:limit]


def _build_reply_rag_messages(
    *,
    conversation_id: str,
    buyer_message: str,
    tone: str,
    rag_results: list[dict[str, Any]],
) -> list[dict[str, str]]:
    context_lines = []
    for idx, result in enumerate(rag_results, start=1):
        context_lines.append(
            f"[{idx}] score={result.get('score', 0):.4f} source={result.get('source') or REPLY_KB_SOURCE}\n"
            f"{result.get('content') or ''}"
        )
    context_text = "\n\n".join(context_lines)
    requested_categories = _infer_reply_categories(buyer_message)
    return [
        {
            "role": "system",
            "content": (
                "You are writing one seller reply draft for Xianyu/Goofish. "
                "Use only the retrieved knowledge below. Do not invent product facts, shipping promises, "
                "price concessions, or configuration details that are not grounded in the knowledge. "
                "If a fact is not confirmed, say it needs confirmation from the real item, label, or extra photos. "
                "Return valid JSON only, with keys: reply_text and covered_points. "
                "reply_text must be colloquial Chinese, polite, concise, single paragraph, no markdown, "
                f"no bullets, no headings, and no more than {REPLY_MAX_CHARS} Chinese characters. "
                "covered_points must be an array containing zero or more of: negotiation, product_info, shipping, condition. "
                "The reply must cover the buyer's asked points when the knowledge supports them."
            ),
        },
        {
            "role": "user",
            "content": (
                f"conversation_id: {conversation_id}\n"
                f"buyer_message: {buyer_message}\n"
                f"tone: {tone}\n"
                f"requested_points: {json.dumps(requested_categories, ensure_ascii=False)}\n\n"
                "retrieved_knowledge:\n"
                f"{context_text}\n\n"
                "Return JSON only. Example:\n"
                '{"reply_text":"可以小刀一点，具体轴体以实物和铭牌为准，我这边付款后24小时内安排发出，最晚不超过48小时。","covered_points":["negotiation","product_info","shipping"]}'
            ),
        },
    ]


@router.get("/competitors")
async def get_xianyu_competitors(
    keyword: str,
    limit: int = 20,
):
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword required")
    try:
        return await _invoke_tool_json(fetch_xianyu_competitors, {"keyword": keyword, "limit": limit})
    except Exception as exc:
        logger.exception("xianyu competitors API failed: keyword=%s", keyword)
        raise HTTPException(status_code=502, detail=f"Failed to fetch Goofish competitors: {exc}") from exc


@router.post("/listing-plan")
async def generate_xianyu_listing_plan(
    req: ListingPlanRequest,
):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword required")

    # 实时抓取竞品数据，不从数据库读取
    try:
        competitor_result = await _invoke_tool_json(fetch_xianyu_competitors, {"keyword": keyword, "limit": req.competitor_limit})
        competitors = competitor_result.get("items", [])
    except Exception as exc:
        logger.exception("Failed to fetch real-time competitors for listing plan: keyword=%s", keyword)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch real-time competitors: {exc}"
        ) from exc

    if not competitors:
        raise HTTPException(
            status_code=404,
            detail=f"No competitors found for keyword={keyword}; try a different keyword.",
        )

    stats = _competitor_price_stats(competitors)
    if stats["priced_count"] == 0:
        raise HTTPException(
            status_code=422,
            detail=f"Competitors for keyword={keyword} do not contain usable price fields.",
        )

    prompt_competitors = _select_competitors_for_prompt(competitors)
    prompt_stats = _competitor_price_stats(prompt_competitors)

    config = ConfigManager.get_llm_config()
    if not config.get("base_url") or not config.get("api_key") or not config.get("model"):
        raise HTTPException(status_code=503, detail="LLM not configured; listing plan generation is unavailable.")

    messages = _build_listing_plan_messages(
        keyword=keyword,
        product_info=req.product_info.strip(),
        cost=req.cost,
        target_margin=req.target_margin,
        competitors=prompt_competitors,
        stats=stats,
    )
    prompt_chars = sum(len(message["content"]) for message in messages)
    logger.info(
        "xianyu listing plan LLM request: keyword=%s competitors=%s model=%s protocol=%s prompt_chars=%s",
        keyword,
        len(prompt_competitors),
        config.get("model"),
        config.get("protocol"),
        prompt_chars,
    )

    client = create_llm_client(
        protocol=config["protocol"],
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
    )
    started_at = time.time()
    response = await client.chat(messages, temperature=0.35)
    duration_ms = int((time.time() - started_at) * 1000)

    if not response or not response.strip():
        llm_error = client.get_last_error() if hasattr(client, "get_last_error") else None
        if llm_error:
            message = llm_error.get("message") or "LLM returned an empty listing plan."
            code = llm_error.get("code")
            status_code = llm_error.get("status_code")
            detail = f"LLM request failed: {message}"
            if code:
                detail = f"{detail} (code={code})"
            logger.error(
                "xianyu listing plan LLM failed: keyword=%s model=%s status_code=%s code=%s detail=%s",
                keyword,
                config.get("model"),
                status_code,
                code,
                message,
            )
            raise HTTPException(status_code=502, detail=detail)
        raise HTTPException(status_code=502, detail="LLM returned an empty listing plan.")

    plan = _parse_listing_plan_response(response)
    result = {
        "status": "success",
        "mode": "llm",
        "keyword": keyword,
        "competitors_used": prompt_competitors,
        "competitor_stats": stats,
        "prompt_competitor_stats": prompt_stats,
        "prompt_competitor_count": len(prompt_competitors),
        "plan": plan,
        "raw_response": response.strip(),
        "llm_call": {
            "provider": config.get("provider"),
            "protocol": config.get("protocol"),
            "model": config.get("model"),
            "request_message_count": len(messages),
            "prompt_chars": prompt_chars,
            "response_chars": len(response),
            "duration_ms": duration_ms,
            "called_at": datetime.now().isoformat(),
        },
    }


@router.post("/reply-draft")
async def create_reply_draft(
    req: DraftReplyRequest,
):
    # 机器人闭嘴：检查会话状态
    from core.conversation_status import should_bot_reply, get_conversation_status

    if not should_bot_reply(req.conversation_id):
        current_status = get_conversation_status(req.conversation_id)
        return {
            "status": "bot_muted",
            "conversation_id": req.conversation_id,
            "conversation_status": current_status,
            "draft": "",
            "detail": f"会话状态为 '{current_status}'，机器人不应回复。请人工处理。"
        }

    config = ConfigManager.get_llm_config()
    if not config.get("base_url") or not config.get("api_key") or not config.get("model"):
        raise HTTPException(status_code=503, detail="LLM not configured; reply draft generation is unavailable.")

    client = create_llm_client(
        protocol=config["protocol"],
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
    )
    try:
        seed_info = await asyncio.to_thread(_upsert_reply_seed_knowledge)
        rag = await _strict_reply_rag_search(req.buyer_message, REPLY_QUERY_TOP_K)
    except Exception as exc:
        logger.exception("reply draft RAG preparation failed")
        raise HTTPException(status_code=502, detail=f"Reply RAG preparation failed: {exc}") from exc

    if not rag["hit"]:
        # 检索护栏触发：标记转人工
        from core.conversation_status import mark_conversation_pending_handoff

        confidence_status = rag.get("confidence_status", "not_found")
        top_score = rag.get("top_score", 0.0)

        # 生成 holding 建议话术
        if confidence_status == "gray":
            suggested_reply = "您好，这个问题我需要再确认一下，稍后回复您"
        else:
            suggested_reply = "您好，这个问题我帮您确认一下，稍后回复您"

        # 标记会话为待人工处理
        try:
            handoff_result = mark_conversation_pending_handoff(
                conversation_id=req.conversation_id,
                reason=confidence_status,
                buyer_message=req.buyer_message,
                confidence_score=top_score,
                suggested_reply=suggested_reply
            )
            logger.info(
                "Conversation marked for handoff: conversation_id=%s, reason=%s, score=%.4f",
                req.conversation_id, confidence_status, top_score
            )
        except Exception as exc:
            logger.exception("Failed to mark conversation for handoff: %s", exc)
            # 即使标记失败，也继续返回结果

        return {
            "status": "no_knowledge",
            "conversation_id": req.conversation_id,
            "draft": "",
            "mode": "rag_llm",
            "detail": "无相关知识，未生成回复。",
            "handoff": {
                "status": "pending_handoff",
                "reason": confidence_status,
                "suggested_reply": suggested_reply,
                "note": "⚠️ 请手动复制以上话术发送给买家"
            },
            "knowledge_seed": seed_info,
            "rag": {
                "hit": False,
                "threshold": rag["threshold"],
                "top_score": rag["top_score"],
                "confidence_status": confidence_status,
                "results": [
                    {
                        "id": item.get("id"),
                        "score": round(float(item.get("score", 0.0)), 4),
                        "source": item.get("source"),
                        "content": item.get("content"),
                        "category": (item.get("metadata") or {}).get("category"),
                    }
                    for item in rag["results"]
                ],
                "embedding_model": seed_info["embedding_model"],
                "embedding_dimension": seed_info["embedding_dimension"],
                "reranker_model": seed_info["reranker_model"],
                "engine": rag["engine_stats"],
                "retrieval_path": rag["retrieval_path"],
            },
        }

    messages = _build_reply_rag_messages(
        conversation_id=req.conversation_id,
        buyer_message=req.buyer_message,
        tone=req.tone,
        rag_results=rag["results"],
    )
    prompt_chars = sum(len(message["content"]) for message in messages)
    started_at = time.time()
    reply = await client.chat(messages, temperature=0.1)
    duration_ms = int((time.time() - started_at) * 1000)

    if not reply or not reply.strip():
        llm_error = client.get_last_error() if hasattr(client, "get_last_error") else None
        if llm_error:
            message = llm_error.get("message") or "LLM returned an empty reply draft."
            code = llm_error.get("code")
            detail = f"LLM request failed: {message}"
            if code:
                detail = f"{detail} (code={code})"
            raise HTTPException(status_code=502, detail=detail)
        raise HTTPException(status_code=502, detail="LLM returned an empty reply draft.")

    finalized = _finalize_reply_response(reply)
    final_reply = finalized["reply_text"]
    if not final_reply:
        raise HTTPException(status_code=502, detail="LLM returned a reply but the cleaned final reply is empty.")

    try:
        save_draft(req.conversation_id, final_reply)
    except ValueError:
        logger.info("reply draft generated without persisted buyer message: conversation_id=%s", req.conversation_id)

    result = {
        "status": "success",
        "conversation_id": req.conversation_id,
        "draft": final_reply,
        "mode": "rag_llm",
        "knowledge_seed": seed_info,
        "rag": {
            "hit": True,
            "threshold": rag["threshold"],
            "top_score": rag["top_score"],
            "results": [
                {
                    "id": item.get("id"),
                    "score": round(float(item.get("score", 0.0)), 4),
                    "source": item.get("source"),
                    "content": item.get("content"),
                    "category": (item.get("metadata") or {}).get("category"),
                }
                for item in rag["results"]
            ],
            "embedding_model": seed_info["embedding_model"],
            "embedding_dimension": seed_info["embedding_dimension"],
            "reranker_model": seed_info["reranker_model"],
            "engine": rag["engine_stats"],
            "retrieval_path": rag["retrieval_path"],
        },
        "llm_call": {
            "provider": config.get("provider"),
            "protocol": config.get("protocol"),
            "model": config.get("model"),
            "request_message_count": len(messages),
            "prompt_chars": prompt_chars,
            "response_chars": len(reply),
            "duration_ms": duration_ms,
            "called_at": datetime.now().isoformat(),
        },
        "reply_generation": {
            "parse_status": finalized["parse_status"],
            "covered_points": finalized["covered_points"],
            "post_process": finalized["post_process"],
            "raw_response_preview": finalized["raw_response"][:400],
        },
    }
    await asyncio.to_thread(
        save_generated_artifact,
        artifact_type="reply_draft",
        conversation_id=req.conversation_id,
        payload=result,
        source_label="real",
        summary_text=_artifact_summary_text(result),
    )
    return result


@router.post("/marketing-plan")
async def generate_xianyu_marketing_plan(
    req: MarketingPlanRequest,
):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword required")

    # 实时抓取竞品数据，不从数据库读取
    try:
        competitor_result = await _invoke_tool_json(fetch_xianyu_competitors, {"keyword": keyword, "limit": req.competitor_limit})
        competitors = competitor_result.get("items", [])
    except Exception as exc:
        logger.exception("Failed to fetch real-time competitors for marketing plan: keyword=%s", keyword)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch real-time competitors: {exc}"
        ) from exc

    if not competitors:
        raise HTTPException(
            status_code=404,
            detail=f"No competitors found for keyword={keyword}; try a different keyword.",
        )

    stats = _competitor_price_stats(competitors)
    if stats["priced_count"] == 0:
        raise HTTPException(
            status_code=422,
            detail=f"Competitors for keyword={keyword} do not contain usable price fields.",
        )

    config = ConfigManager.get_llm_config()
    if not config.get("base_url") or not config.get("api_key") or not config.get("model"):
        raise HTTPException(status_code=503, detail="LLM not configured; marketing plan generation is unavailable.")

    prompt_competitors = _select_competitors_for_prompt(competitors)
    listing_messages = _build_listing_plan_messages(
        keyword=keyword,
        product_info=req.product_info.strip(),
        cost=None,
        target_margin=None,
        competitors=prompt_competitors,
        stats=stats,
    )
    listing_prompt_chars = sum(len(message["content"]) for message in listing_messages)

    client = create_llm_client(
        protocol=config["protocol"],
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
    )

    listing_started_at = time.time()
    listing_response = await client.chat(listing_messages, temperature=0.35)
    listing_duration_ms = int((time.time() - listing_started_at) * 1000)
    if not listing_response or not listing_response.strip():
        llm_error = client.get_last_error() if hasattr(client, "get_last_error") else None
        if llm_error:
            message = llm_error.get("message") or "LLM returned an empty listing plan."
            code = llm_error.get("code")
            detail = f"LLM request failed: {message}"
            if code:
                detail = f"{detail} (code={code})"
            raise HTTPException(status_code=502, detail=detail)
        raise HTTPException(status_code=502, detail="LLM returned an empty listing plan.")

    listing_plan = _parse_listing_plan_response(listing_response)
    normalized_listing = _normalize_listing_plan_for_marketing(listing_plan, keyword)

    marketing_messages = _build_marketing_plan_messages(
        keyword=keyword,
        listing_plan=normalized_listing,
        competitors=competitors,
        stats=stats,
    )
    marketing_prompt_chars = sum(len(message["content"]) for message in marketing_messages)
    marketing_started_at = time.time()
    marketing_response = await client.chat(marketing_messages, temperature=0.25)
    marketing_duration_ms = int((time.time() - marketing_started_at) * 1000)
    if not marketing_response or not marketing_response.strip():
        llm_error = client.get_last_error() if hasattr(client, "get_last_error") else None
        if llm_error:
            message = llm_error.get("message") or "LLM returned an empty marketing plan."
            code = llm_error.get("code")
            detail = f"LLM request failed: {message}"
            if code:
                detail = f"{detail} (code={code})"
            raise HTTPException(status_code=502, detail=detail)
        raise HTTPException(status_code=502, detail="LLM returned an empty marketing plan.")

    marketing_plan = _parse_marketing_plan_response(marketing_response)
    result = {
        "status": "success",
        "mode": "llm",
        "keyword": keyword,
        "product_info": normalized_listing,
        "competitor_stats": stats,
        "competitors_used": prompt_competitors,
        "hot_competitors": _select_hot_competitors(competitors, limit=5),
        "listing_plan": listing_plan,
        "marketing_plan": marketing_plan,
        "listing_plan_call": {
            "provider": config.get("provider"),
            "protocol": config.get("protocol"),
            "model": config.get("model"),
            "request_message_count": len(listing_messages),
            "prompt_chars": listing_prompt_chars,
            "response_chars": len(listing_response),
            "duration_ms": listing_duration_ms,
            "called_at": datetime.now().isoformat(),
        },
        "llm_call": {
            "provider": config.get("provider"),
            "protocol": config.get("protocol"),
            "model": config.get("model"),
            "request_message_count": len(marketing_messages),
            "prompt_chars": marketing_prompt_chars,
            "response_chars": len(marketing_response),
            "duration_ms": marketing_duration_ms,
            "called_at": datetime.now().isoformat(),
        },
    }
    await asyncio.to_thread(
        save_generated_artifact,
        artifact_type="marketing_plan",
        keyword=keyword,
        payload=result,
        source_label="real",
        summary_text=_artifact_summary_text(result),
    )
    return result


@router.post("/profit-analysis")
async def generate_xianyu_profit_analysis(
    req: ProfitAnalysisRequest,
):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword required")

    # 实时抓取竞品数据，不从数据库读取
    try:
        competitor_result = await _invoke_tool_json(fetch_xianyu_competitors, {"keyword": keyword, "limit": req.competitor_limit})
        competitors = competitor_result.get("items", [])
    except Exception as exc:
        logger.exception("Failed to fetch real-time competitors for profit analysis: keyword=%s", keyword)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch real-time competitors: {exc}"
        ) from exc

    if not competitors:
        raise HTTPException(
            status_code=404,
            detail=f"No competitors found for keyword={keyword}; try a different keyword.",
        )

    stats = _competitor_price_stats(competitors)
    if stats["priced_count"] == 0:
        raise HTTPException(
            status_code=422,
            detail=f"Real competitors for keyword={keyword} do not contain usable price fields.",
        )

    hot_competitors = _select_hot_competitors(competitors, limit=5)
    price_bands = _build_price_band_distribution(competitors)
    assumed_cost = round(float(req.assumed_cost), 2) if req.assumed_cost is not None else None
    profit_scenarios = _build_profit_scenarios(assumed_cost=assumed_cost, stats=stats)

    config = ConfigManager.get_llm_config()
    if not config.get("base_url") or not config.get("api_key") or not config.get("model"):
        raise HTTPException(status_code=503, detail="LLM not configured; profit analysis is unavailable.")

    prompt_competitors = _select_competitors_for_prompt(competitors, max_items=10, max_total_chars=3400)
    messages = _build_profit_analysis_messages(
        keyword=keyword,
        competitors=prompt_competitors,
        stats=stats,
        hot_competitors=hot_competitors,
        price_bands=price_bands,
        assumed_cost=assumed_cost,
        profit_scenarios=profit_scenarios,
    )
    prompt_chars = sum(len(message["content"]) for message in messages)

    client = create_llm_client(
        protocol=config["protocol"],
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
    )
    started_at = time.time()
    response = await client.chat(messages, temperature=0.2)
    duration_ms = int((time.time() - started_at) * 1000)
    if not response or not response.strip():
        llm_error = client.get_last_error() if hasattr(client, "get_last_error") else None
        if llm_error:
            message = llm_error.get("message") or "LLM returned an empty profit analysis."
            code = llm_error.get("code")
            detail = f"LLM request failed: {message}"
            if code:
                detail = f"{detail} (code={code})"
            raise HTTPException(status_code=502, detail=detail)
        raise HTTPException(status_code=502, detail="LLM returned an empty profit analysis.")

    analysis = _parse_profit_analysis_response(response)
    result = {
        "status": "success",
        "mode": "llm",
        "keyword": keyword,
        "assumption": {
            "assumed_cost": assumed_cost,
            "is_hypothetical": assumed_cost is not None,
            "note": profit_scenarios["detail"],
        },
        "competitor_stats": stats,
        "hot_competitors": hot_competitors,
        "price_band_distribution": price_bands,
        "profit_scenarios": profit_scenarios,
        "competitors_used": prompt_competitors,
        "analysis": analysis,
        "llm_call": {
            "provider": config.get("provider"),
            "protocol": config.get("protocol"),
            "model": config.get("model"),
            "request_message_count": len(messages),
            "prompt_chars": prompt_chars,
            "response_chars": len(response),
            "duration_ms": duration_ms,
            "called_at": datetime.now().isoformat(),
        },
    }
    await asyncio.to_thread(
        save_generated_artifact,
        artifact_type="profit_analysis",
        keyword=keyword,
        payload=result,
        source_label="real",
        summary_text=_artifact_summary_text(result),
    )
    return result
    await asyncio.to_thread(
        save_generated_artifact,
        artifact_type="listing_plan",
        keyword=keyword,
        payload=result,
        source_label="real",
        summary_text=_artifact_summary_text(result),
    )
    return result


@router.post("/send-reply")
async def submit_xianyu_reply(
    req: SendReplyRequest,
):
    try:
        return await _invoke_tool_json(
            send_xianyu_reply,
            {
                "conversation_id": req.conversation_id,
                "content": req.content,
                "approval_id": req.approval_id,
            },
        )
    except Exception as exc:
        logger.exception("xianyu send-reply API failed: conversation_id=%s", req.conversation_id)
        raise HTTPException(status_code=502, detail=f"Failed to submit Goofish reply: {exc}") from exc


@router.post("/list-item")
async def submit_xianyu_listing(
    req: ListItemRequest,
):
    try:
        return await _invoke_tool_json(
            list_xianyu_item,
            {
                "item_json": json.dumps(req.item, ensure_ascii=False),
                "approval_id": req.approval_id,
            },
        )
    except Exception as exc:
        logger.exception("xianyu list-item API failed")
        raise HTTPException(status_code=502, detail=f"Failed to submit Goofish listing action: {exc}") from exc


@router.post("/ship-order")
async def submit_xianyu_shipping(
    req: ShipOrderRequest,
):
    try:
        return await _invoke_tool_json(
            ship_xianyu_order,
            {
                "order_id": req.order_id,
                "shipment_json": json.dumps(req.shipment, ensure_ascii=False),
                "approval_id": req.approval_id,
            },
        )
    except Exception as exc:
        logger.exception("xianyu ship-order API failed: order_id=%s", req.order_id)
        raise HTTPException(status_code=502, detail=f"Failed to submit Goofish shipping action: {exc}") from exc


@router.get("/debug/dump-conversations")
async def dump_conversations_dom():
    """临时端点：dump消息中心的真实DOM结构，用于确定selector"""
    try:
        from platforms.goofish_playwright import GoofishPlaywrightPlatform
        from pathlib import Path
        import time

        platform = GoofishPlaywrightPlatform()

        def action(page):
            result = {
                "initial_url": page.url,
                "message_url": None,
                "in_iframe": False,
                "conversations_found": False,
                "dump_files": [],
                "conversation_items": [],
            }

            # 1. 尝试导航到消息中心
            logger.info("尝试导航到消息中心...")

            # 先尝试常见的消息URL
            possible_urls = [
                "https://h5.m.goofish.com/message",
                "https://h5.m.goofish.com/im",
                "https://goofish.com/message",
            ]

            for url in possible_urls:
                try:
                    logger.info(f"尝试URL: {url}")
                    page.goto(url, wait_until='domcontentloaded', timeout=10000)
                    time.sleep(2)
                    result["message_url"] = page.url
                    logger.info(f"✅ 消息中心URL: {page.url}")
                    break
                except Exception as exc:
                    logger.warning(f"URL {url} 失败: {exc}")
                    continue

            # 2. dump完整HTML
            dump_dir = Path("logs")
            dump_dir.mkdir(exist_ok=True)

            full_html = page.content()
            full_dump_path = dump_dir / "goofish_conversations_full.html"
            with open(full_dump_path, 'w', encoding='utf-8') as f:
                f.write(full_html)
            result["dump_files"].append(str(full_dump_path))
            logger.info(f"✅ 完整HTML已保存: {full_dump_path}")

            # 3. 截图
            screenshot_path = dump_dir / "goofish_conversations_screenshot.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            result["dump_files"].append(str(screenshot_path))
            logger.info(f"✅ 截图已保存: {screenshot_path}")

            # 4. 尝试找会话列表并dump前3项
            # 搜索可能的会话项
            test_selectors = [
                '[class*="conversation"]',
                '[class*="message"]',
                '[class*="chat"]',
                '[class*="session"]',
                'li',
                '[class*="item"]',
            ]

            for selector in test_selectors:
                try:
                    items = page.locator(selector).all()
                    if len(items) > 0 and len(items) < 100:  # 避免太多
                        logger.info(f"找到{len(items)}个元素: {selector}")

                        # 获取前3个的outerHTML
                        for i in range(min(3, len(items))):
                            try:
                                outer_html = items[i].evaluate('el => el.outerHTML')
                                result["conversation_items"].append({
                                    "index": i,
                                    "selector": selector,
                                    "html": outer_html[:500]  # 截断，避免太长
                                })
                                logger.info(f"\n========== 项目 {i+1} (selector: {selector}) ==========\n{outer_html[:300]}...\n")
                            except:
                                continue

                        if len(result["conversation_items"]) > 0:
                            result["conversations_found"] = True
                            break
                except Exception as exc:
                    logger.debug(f"selector {selector} 失败: {exc}")
                    continue

            return result

        result = platform.browser_manager.with_page("dump_conversations", action)
        return {
            "status": "success",
            "data": result,
            "detail": "DOM dump完成，请查看logs/目录"
        }

    except Exception as exc:
        logger.exception("dump conversations failed")
        return {
            "status": "error",
            "detail": str(exc)
        }


# ==================== 会话状态管理 ====================

class HandoffRequest(BaseModel):
    conversation_id: str


class ResolveRequest(BaseModel):
    conversation_id: str


@router.post("/conversations/{conversation_id}/handoff")
async def handoff_conversation(conversation_id: str):
    """人工接手会话（pending_handoff → human_taking）"""
    from core.conversation_status import handoff_to_human

    try:
        result = handoff_to_human(conversation_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to handoff conversation: %s", conversation_id)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/conversations/{conversation_id}/resolve")
async def resolve_conversation(conversation_id: str):
    """标记会话已解决（human_taking → resolved）"""
    from core.conversation_status import resolve_conversation as resolve_conv

    try:
        result = resolve_conv(conversation_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to resolve conversation: %s", conversation_id)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/conversations/{conversation_id}/return-to-bot")
async def return_conversation_to_bot(conversation_id: str):
    """手动交回机器人（human_taking / resolved → open）"""
    from core.conversation_status import return_to_bot

    try:
        result = return_to_bot(conversation_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to return conversation to bot: %s", conversation_id)
        raise HTTPException(status_code=500, detail=str(exc))
