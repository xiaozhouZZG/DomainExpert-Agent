"""
集成测试 — /api/kb/search 必须返回三段式护栏字段（decision/confidence/thresholds）

行为契约（参考 api/knowledge.py:15-28 + knowledge/retrieval_gateway.py:63-151）：
  POST /api/kb/search?query=...&top_k=...
  → 走 search_with_confidence
  → 返回 {query, results, confidence_score, decision, action, thresholds, message}

不能再退回 rag.search(threshold=0) 的旧链路（绕过护栏 = R-03 P0）。

全程 mock：不加载 FAISS、不加载 BM25、不调真 LLM。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """构造一个仅挂 knowledge router 的 FastAPI app，避免拉起整个 app.py。"""
    from fastapi import FastAPI
    from api.knowledge import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _fake_thresholds():
    return {
        "high_threshold": 0.60,
        "low_threshold": 0.53,
        "gray_action": "handoff",
    }


def test_search_returns_high_decision(client):
    """top1 score >= 0.60 → decision=high, action=answer"""
    fake_payload = {
        "status": "high",
        "confidence_score": 0.82,
        "results": [{"chunk_id": "c1", "content": "保修一年", "score": 0.82}],
        "action": "answer",
        "message": None,
        "threshold_config": _fake_thresholds(),
    }
    with patch(
        "knowledge.retrieval_gateway.search_with_confidence",
        return_value=fake_payload,
    ) as mock_swc:
        resp = client.post("/api/kb/search?query=保修多久&top_k=5")

    assert resp.status_code == 200
    body = resp.json()

    # —— 必须出现的护栏字段（R-03 关键断言）——
    assert "decision" in body, "返回必须含 decision"
    assert "confidence_score" in body, "返回必须含 confidence_score"
    assert "thresholds" in body, "返回必须含 thresholds（阈值配置）"
    assert "action" in body
    assert "message" in body

    assert body["decision"] == "high"
    assert body["confidence_score"] == 0.82
    assert body["action"] == "answer"
    assert body["thresholds"]["high_threshold"] == 0.60
    assert body["thresholds"]["low_threshold"] == 0.53
    assert body["query"] == "保修多久"
    mock_swc.assert_called_once()


def test_search_returns_gray_decision(client):
    """0.53 <= top1 < 0.60 → decision=gray, action=handoff"""
    fake_payload = {
        "status": "gray",
        "confidence_score": 0.56,
        "results": [{"chunk_id": "c1", "content": "...", "score": 0.56}],
        "action": "handoff",
        "message": "这个问题我不太确定，建议转人工处理",
        "threshold_config": _fake_thresholds(),
    }
    with patch(
        "knowledge.retrieval_gateway.search_with_confidence",
        return_value=fake_payload,
    ):
        resp = client.post("/api/kb/search?query=能讲价吗&top_k=3")

    body = resp.json()
    assert body["decision"] == "gray"
    assert body["action"] == "handoff"
    assert body["confidence_score"] == 0.56
    assert body["message"] == "这个问题我不太确定，建议转人工处理"
    # 灰区也必须把阈值带回去（前端可显示）
    assert body["thresholds"]["high_threshold"] == 0.60


def test_search_returns_not_found_decision(client):
    """top1 < 0.53 → decision=not_found, action=handoff/fallback"""
    fake_payload = {
        "status": "not_found",
        "confidence_score": 0.31,
        "results": [],
        "action": "handoff",
        "message": "抱歉，这个问题我没有找到可靠答案，帮您转人工处理",
        "threshold_config": _fake_thresholds(),
    }
    with patch(
        "knowledge.retrieval_gateway.search_with_confidence",
        return_value=fake_payload,
    ):
        resp = client.post("/api/kb/search?query=完全无关的问题&top_k=5")

    body = resp.json()
    assert body["decision"] == "not_found"
    assert body["results"] == []
    assert body["action"] == "handoff"
    assert body["confidence_score"] == 0.31


def test_search_endpoint_routes_through_search_with_confidence(client):
    """硬契约：/api/kb/search 必须走 search_with_confidence，不许直接 rag.search(threshold=0)"""
    fake_payload = {
        "status": "high",
        "confidence_score": 0.71,
        "results": [{"chunk_id": "x", "content": "y", "score": 0.71}],
        "action": "answer",
        "message": None,
        "threshold_config": _fake_thresholds(),
    }
    with patch(
        "knowledge.retrieval_gateway.search_with_confidence",
        return_value=fake_payload,
    ) as mock_swc:
        resp = client.post("/api/kb/search?query=X&top_k=5")

    assert resp.status_code == 200
    # 必须被调用过一次 — 即未绕过护栏
    assert mock_swc.call_count == 1
    # 调用参数应带 query/top_k
    call_kwargs = mock_swc.call_args.kwargs
    assert call_kwargs.get("query") == "X"
    assert call_kwargs.get("top_k") == 5
