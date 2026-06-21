"""Realtime dashboard APIs and websocket stream."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.xianyu import _login_state_for_task, _public_login_task
from core.xianyu_service import (
    count_generated_artifacts,
    count_on_sale_listings,
    latest_generated_artifact,
    list_listings,
)
from database.connection import get_db_connection

router = APIRouter(tags=["dashboard"])


def _safe_json(value: Optional[str]):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


@router.get("/api/dashboard/overview")
async def get_dashboard_overview():
    login_task = _public_login_task()
    login_state = _login_state_for_task(login_task)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # ========================================
        # 实时快照数据（不依赖数据库闲鱼数据）
        # ========================================

        # 实时在售商品数
        on_sale_items = count_on_sale_listings()

        # AI生成方案数（保留，这是系统生成的）
        generated_artifact_count = count_generated_artifacts()

        # 待审批数（保留，这是系统流程）
        cursor.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'")
        pending_approvals = cursor.fetchone()[0] or 0

        # 执行日志（保留，用于展示操作历史）
        cursor.execute(
            """
            SELECT event_id, event_time, component, action_name, status, duration_ms,
                   session_id, trace_id, conversation_id, message_id, approval_id,
                   screenshot_path, data_source, detail, error, metadata_json
            FROM execution_logs
            ORDER BY id DESC
            LIMIT 30
            """
        )
        execution_logs = [
            {
                "event_id": row[0],
                "timestamp": row[1],
                "component": row[2],
                "action_name": row[3],
                "status": row[4],
                "duration_ms": row[5],
                "session_id": row[6],
                "trace_id": row[7],
                "conversation_id": row[8],
                "message_id": row[9],
                "approval_id": row[10],
                "screenshot_path": row[11],
                "data_source": row[12],
                "detail": row[13],
                "error": row[14],
                "metadata": _safe_json(row[15]) or {},
            }
            for row in cursor.fetchall()
        ]

        # 审批列表（保留，这是系统流程）
        cursor.execute(
            """
            SELECT approval_id, workflow_type, customer_id, order_id, status, created_at, title, content
            FROM approvals
            ORDER BY id DESC
            LIMIT 20
            """
        )
        approvals = [
            {
                "approval_id": row[0],
                "workflow_type": row[1],
                "customer_id": row[2],
                "order_id": row[3],
                "status": row[4],
                "created_at": row[5],
                "title": row[6],
                "content": row[7],
                "data_source": "system",
            }
            for row in cursor.fetchall()
        ]

        # 实时在售商品列表
        listings = list_listings(limit=20)

        # AI生成的方案（保留，这是系统生成的）
        latest_listing_plan = latest_generated_artifact("listing_plan")
        latest_marketing_plan = latest_generated_artifact("marketing_plan")
        latest_profit_analysis = latest_generated_artifact("profit_analysis")
        latest_reply_draft = latest_generated_artifact("reply_draft")

        return {
            "platform_mode": "goofish",
            "login_state": login_state,
            "login_task": login_task,
            "metrics": {
                "today_messages": 0,              # 消息功能未开发
                "unreplied_messages": 0,          # 消息功能未开发
                "pending_approvals": pending_approvals,
                "conversation_count": 0,          # 消息功能未开发
                "competitor_records": 0,          # 竞品改为实时抓取，不再统计历史
                "competitor_keywords": 0,         # 竞品改为实时抓取，不再统计历史
                "on_sale_items": on_sale_items,
                "generated_artifacts": generated_artifact_count,
            },
            "latest_results": {
                "listing_plan": latest_listing_plan["payload"] if latest_listing_plan else None,
                "marketing_plan": latest_marketing_plan["payload"] if latest_marketing_plan else None,
                "profit_analysis": latest_profit_analysis["payload"] if latest_profit_analysis else None,
                "reply_draft": latest_reply_draft["payload"] if latest_reply_draft else None,
            },
            "execution_logs": execution_logs,
            "conversations": [],        # 消息功能未开发，实时读取
            "messages": [],             # 消息功能未开发，实时读取
            "approvals": approvals,     # 保留，系统审批流程
            "competitors": [],          # 竞品改为实时抓取，不再从数据库读
            "listings": listings,       # 保留，实时读取在售商品
        }
    finally:
        conn.close()


@router.get("/api/dashboard/execution-logs")
async def get_execution_logs(
    limit: int = Query(50, ge=1, le=200),
):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT event_id, event_time, component, action_name, status, duration_ms,
                   session_id, trace_id, conversation_id, message_id, approval_id,
                   screenshot_path, data_source, detail, error, metadata_json
            FROM execution_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return {
            "logs": [
                {
                    "event_id": row[0],
                    "timestamp": row[1],
                    "component": row[2],
                    "action_name": row[3],
                    "status": row[4],
                    "duration_ms": row[5],
                    "session_id": row[6],
                    "trace_id": row[7],
                    "conversation_id": row[8],
                    "message_id": row[9],
                    "approval_id": row[10],
                    "screenshot_path": row[11],
                    "data_source": row[12],
                    "detail": row[13],
                    "error": row[14],
                    "metadata": _safe_json(row[15]) or {},
                }
                for row in cursor.fetchall()
            ]
        }
    finally:
        conn.close()
