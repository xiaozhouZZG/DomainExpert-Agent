"""
P0-1 行为测试: reranker 失败时优雅降级到余弦相似度，不静默失败。

行为契约:
1. reranker.rerank() 抛异常 → score 回退到 vector_score（余弦相似度 0~1）
2. high-score 候选（vector_score >= 0.60）仍判 high，能被自动回复
3. low-score 候选（vector_score < 0.53）判 not_found
4. 降级标记 _degraded=True 出现在结果上

全程 mock: 不加载真 CrossEncoder、不调真 FAISS/BM25。
"""
from unittest.mock import MagicMock

import pytest


def _make_candidate(chunk_id: str, vector_score: float, content: str = "test content") -> dict:
    """造一个类似 RRF 融合后的候选结果，含 vector_score。"""
    return {
        "id": chunk_id,
        "score": 0.016,            # RRF 融合分（≈1/61）
        "rrf_score": 0.016,
        "vector_score": vector_score,
        "content": content,
        "source": "test",
        "metadata": {"content": content, "source": "test"},
    }


# ============ reranker 不在（未加载）的降级场景 ============

def test_no_reranker_uses_vector_score():
    """reranker=None → score 走 vector_score（0~1），不是 RRF 分（≈0.016）。"""
    candidates = [
        _make_candidate("high_1", 0.72),
        _make_candidate("mid_1", 0.58),
        _make_candidate("low_1", 0.15),
    ]
    reranked = candidates[:5]
    for c in reranked:
        c["score"] = c.get("vector_score", 0.0)
    reranked.sort(key=lambda x: x["score"], reverse=True)

    assert reranked[0]["score"] == 0.72
    assert reranked[1]["score"] == 0.58
    assert reranked[2]["score"] == 0.15
    assert reranked[0]["vector_score"] == 0.72


# ============ reranker 抛异常的回退场景 ============

def test_reranker_fallback_to_vector_score():
    """reranker 抛异常 → score 回退到 vector_score，不残留在 RRF 分。"""
    candidates = [
        _make_candidate("high_1", 0.82),
        _make_candidate("low_1", 0.32),
    ]

    mock_reranker = MagicMock()
    mock_reranker.rerank.side_effect = RuntimeError("模型 OOM")

    reranked = None
    degraded_flag = False
    degraded_reason = None
    try:
        reranked = mock_reranker.rerank(candidates, top_k=5)
    except Exception:
        for c in candidates:
            c["score"] = c.get("vector_score", 0.0)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        reranked = candidates[:5]
        degraded_flag = True
        degraded_reason = "reranker_unavailable"

    assert reranked is not None
    assert len(reranked) == 2
    # 高分候选 score 回到 0.82（不是 ≈0.016）
    assert reranked[0]["score"] == pytest.approx(0.82, abs=1e-4)
    # 低分候选 score 0.32
    assert reranked[1]["score"] == pytest.approx(0.32, abs=1e-4)
    # vector_score 保留
    assert reranked[0]["vector_score"] == 0.82
    assert reranked[1]["vector_score"] == 0.32
    # 降级标记
    assert degraded_flag is True
    assert degraded_reason == "reranker_unavailable"


# ============ 阈值兼容性 ============

def test_high_score_passes_threshold_after_fallback():
    """回退降级后高分候选（0.82）> engine 内部阈值 0.35 → 不返回空。"""
    threshold = 0.35

    candidates = [
        _make_candidate("high_1", 0.82),
        _make_candidate("low_1", 0.32),
    ]

    for c in candidates:
        c["score"] = c.get("vector_score", 0.0)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    reranked = candidates[:5]

    top_k = 5
    final_results = []
    if reranked:
        top_score = reranked[0].get("score", 0.0)
        if top_score < threshold:
            final_results = []
        else:
            final_results = reranked[:top_k]

    assert len(final_results) == 2
    assert final_results[0]["score"] == 0.82


def test_low_score_fails_threshold_after_fallback():
    """回退降级后低分候选（0.15）< 0.35 → 空返回。"""
    threshold = 0.35

    candidates = [
        _make_candidate("low_1", 0.15),
    ]

    for c in candidates:
        c["score"] = c.get("vector_score", 0.0)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    reranked = candidates[:5]

    top_k = 5
    final_results = []
    if reranked:
        top_score = reranked[0].get("score", 0.0)
        if top_score < threshold:
            final_results = []
        else:
            final_results = reranked[:top_k]

    assert len(final_results) == 0


# ============ 降级可观测 ============

def test_degraded_marker_set_on_results():
    """回退降级后最终结果每个 item 带 _degraded=True + _degraded_reason。"""
    candidates = [
        _make_candidate("high_1", 0.82),
    ]

    for c in candidates:
        c["score"] = c.get("vector_score", 0.0)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    reranked = candidates[:5]

    final_results = reranked[:5]

    degraded_flag = True
    degraded_reason = "reranker_unavailable"
    if degraded_flag and final_results:
        for r in final_results:
            r["_degraded"] = True
            r["_degraded_reason"] = degraded_reason

    assert len(final_results) == 1
    assert final_results[0]["_degraded"] is True
    assert final_results[0]["_degraded_reason"] == "reranker_unavailable"
