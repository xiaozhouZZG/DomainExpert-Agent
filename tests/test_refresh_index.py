"""
集成测试 — 文档入库成功后必须刷新索引；刷新失败不返回假成功（R-01）

行为契约（参考 knowledge/processing_queue.py:120-198 + knowledge/hybrid_rag_engine.py:43-51）：
  process_document 成功 → _refresh_index_after_ingest(doc_id, filename) 必调
  _refresh_index_after_ingest 内：
    refresh_index_after_ingest() 成功 → index_status='ok'
    refresh_index_after_ingest() 抛异常 → index_status='refresh_failed' + error_message，
      但 document 处理状态保持 completed（资料确实已入库），异常被捕获不向上传播

全程 mock：不真建 FAISS/BM25，不真写 SQLite chunks 表。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ============ refresh_index_after_ingest 本体行为 ============

def test_refresh_index_after_ingest_resets_then_rebuilds():
    """refresh_index_after_ingest 必须先 reset 单例，再 build_index。"""
    from knowledge import hybrid_rag_engine

    fake_engine = MagicMock()

    with patch.object(hybrid_rag_engine, "reset_hybrid_engine") as mock_reset, \
         patch.object(hybrid_rag_engine, "get_hybrid_engine", return_value=fake_engine) as mock_get:
        hybrid_rag_engine.refresh_index_after_ingest()

    mock_reset.assert_called_once()
    mock_get.assert_called_once()
    fake_engine.build_index.assert_called_once()


def test_refresh_index_propagates_failure():
    """build_index 抛异常时，refresh_index_after_ingest 必须抛出来，由调用方判定（不许吞）。"""
    from knowledge import hybrid_rag_engine

    fake_engine = MagicMock()
    fake_engine.build_index.side_effect = RuntimeError("FAISS 索引文件损坏")

    with patch.object(hybrid_rag_engine, "reset_hybrid_engine"), \
         patch.object(hybrid_rag_engine, "get_hybrid_engine", return_value=fake_engine):
        with pytest.raises(RuntimeError, match="FAISS"):
            hybrid_rag_engine.refresh_index_after_ingest()


# ============ processing_queue._refresh_index_after_ingest 上层包装 ============

@pytest.fixture
def queue_instance():
    """造一个 DocumentProcessingQueue 单例 — __init__ 不自动起 worker，无需 mock。"""
    from knowledge.processing_queue import DocumentProcessingQueue

    q = DocumentProcessingQueue()
    # 单例 — 无论是否 start 过，本测试只调 _refresh_index_after_ingest，不会触发 worker。
    return q


def test_processing_queue_calls_refresh_on_success(queue_instance):
    """入库成功 → refresh_index_after_ingest 被调用 + index_status 标记为 ok。"""
    with patch(
        "knowledge.hybrid_rag_engine.refresh_index_after_ingest"
    ) as mock_refresh, patch.object(
        queue_instance, "_set_index_status"
    ) as mock_set_status:
        queue_instance._refresh_index_after_ingest(doc_id="doc_abc", filename="manual.pdf")

    mock_refresh.assert_called_once()
    # 必须显式把 index_status 标 ok
    mock_set_status.assert_called_once_with("doc_abc", "ok", None)


def test_processing_queue_marks_refresh_failed_no_fake_success(queue_instance):
    """refresh 抛异常时 → index_status='refresh_failed' + error_message，绝不假成功。

    R-01 关键：资料已入库 + 索引刷新失败 ≠ 完全失败；用 index_status 把两个事实分清楚。
    """
    with patch(
        "knowledge.hybrid_rag_engine.refresh_index_after_ingest",
        side_effect=RuntimeError("hnswlib 内存不足"),
    ), patch.object(
        queue_instance, "_set_index_status"
    ) as mock_set_status:
        # 不许向上抛 — 调用方已 try/except，资料入库本身保持成功
        queue_instance._refresh_index_after_ingest(doc_id="doc_xyz", filename="spec.txt")

    # 必须用 'refresh_failed' 标记，error_message 不能为空
    assert mock_set_status.call_count == 1
    args = mock_set_status.call_args.args
    assert args[0] == "doc_xyz"
    assert args[1] == "refresh_failed", f"必须标记为 refresh_failed，实际：{args[1]}"
    assert args[2] is not None and "hnswlib" in args[2], \
        f"error_message 必须含真实原因，实际：{args[2]}"


def test_processing_queue_does_not_call_set_index_ok_on_failure(queue_instance):
    """刷新失败时绝不能调用 set_index_status('ok') — 防止假成功。"""
    calls_with_ok = []

    def fake_set(doc_id, status, err):
        if status == "ok":
            calls_with_ok.append((doc_id, status, err))

    with patch(
        "knowledge.hybrid_rag_engine.refresh_index_after_ingest",
        side_effect=Exception("任意失败"),
    ), patch.object(
        queue_instance, "_set_index_status", side_effect=fake_set
    ):
        queue_instance._refresh_index_after_ingest(doc_id="doc_fail", filename="x.pdf")

    assert calls_with_ok == [], f"刷新失败竟然标了 ok，是假成功：{calls_with_ok}"
