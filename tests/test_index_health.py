"""
第2层 行为测试: 索引健康四态 + retrieval_gateway 信号合并。

契约（断言断到具体取值，非摆设）:
- build_index 缺核心列(embedding)      -> _index_status="build_failed", 不建索引
- build_index 缺可选列(product_name)   -> _index_status="schema_degraded", 索引仍建, degraded_reason 含 product_name
- build_index 列齐                     -> _index_status="ok", degraded_reason is None
- gateway: build_failed 即使有高分结果也强制 not_found；
           schema_degraded 不降级(高分仍 high)只带 degraded 信号；
           index 故障优先于 reranker；ok 时 degraded=False / reason=None。

全程不加载真模型(stub _init_components)、不连真库(tmp sqlite / mock engine)。
真正最可信的证据是「备份副本删列真 search」，本文件是补充。
"""
import sqlite3

import numpy as np
from unittest.mock import MagicMock

from knowledge.hybrid_rag_engine import HybridRAGEngine
from database import connection
from knowledge import retrieval_gateway


# ============ 组1: build_index 列检测四态 ============

def _engine_with_tmpdb(monkeypatch, db_path):
    """构造引擎但跳过真模型加载，DB 指向 tmp 库，向量/BM25 用 mock。"""
    monkeypatch.setattr(HybridRAGEngine, "_init_components", lambda self: None)
    monkeypatch.setattr(connection, "DB_PATH", str(db_path))
    e = HybridRAGEngine()
    e.mode = "vector"
    e.vector_index = MagicMock()
    e.bm25 = MagicMock()
    return e


def _create_documents(cur):
    cur.execute("CREATE TABLE documents (doc_id TEXT, title TEXT)")
    cur.execute("INSERT INTO documents (doc_id, title) VALUES ('d1', 'doc-title')")


def test_build_index_ok_all_columns(tmp_path, monkeypatch):
    db = tmp_path / "ok.db"
    conn = sqlite3.connect(str(db)); cur = conn.cursor()
    _create_documents(cur)
    cur.execute(
        "CREATE TABLE chunks (chunk_id TEXT, doc_id TEXT, text TEXT, embedding BLOB, "
        "category TEXT, source TEXT, business_line TEXT, product_name TEXT)"
    )
    emb = np.ones(4, dtype=np.float32).tobytes()
    cur.execute(
        "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)",
        ("c1", "d1", "hello", emb, "cat", "src", "bl", "pn"),
    )
    conn.commit(); conn.close()

    e = _engine_with_tmpdb(monkeypatch, db)
    e.build_index()

    assert e._index_status == "ok"
    assert e._index_degraded_reason is None
    assert e._index_built is True
    assert e.index_health()["status"] == "ok"
    e.vector_index.add.assert_called_once()


def test_build_index_schema_degraded_missing_product_name(tmp_path, monkeypatch):
    db = tmp_path / "degraded.db"
    conn = sqlite3.connect(str(db)); cur = conn.cursor()
    _create_documents(cur)
    # 缺可选列 product_name
    cur.execute(
        "CREATE TABLE chunks (chunk_id TEXT, doc_id TEXT, text TEXT, embedding BLOB, "
        "category TEXT, source TEXT, business_line TEXT)"
    )
    emb = np.ones(4, dtype=np.float32).tobytes()
    cur.execute(
        "INSERT INTO chunks VALUES (?,?,?,?,?,?,?)",
        ("c1", "d1", "hello", emb, "cat", "src", "bl"),
    )
    conn.commit(); conn.close()

    e = _engine_with_tmpdb(monkeypatch, db)
    e.build_index()

    assert e._index_status == "schema_degraded"   # 索引仍建起来（可用）
    assert e._index_built is True
    assert "schema_missing_columns" in e._index_degraded_reason
    assert "product_name" in e._index_degraded_reason
    assert e.index_health()["degraded_reason"] == e._index_degraded_reason
    e.vector_index.add.assert_called_once()       # NULL 兜底，照常入索引


def test_build_index_build_failed_missing_embedding(tmp_path, monkeypatch):
    db = tmp_path / "failed.db"
    conn = sqlite3.connect(str(db)); cur = conn.cursor()
    _create_documents(cur)
    # 缺核心列 embedding
    cur.execute(
        "CREATE TABLE chunks (chunk_id TEXT, doc_id TEXT, text TEXT, "
        "category TEXT, source TEXT, business_line TEXT, product_name TEXT)"
    )
    cur.execute("INSERT INTO chunks (chunk_id, doc_id, text) VALUES ('c1','d1','hello')")
    conn.commit(); conn.close()

    e = _engine_with_tmpdb(monkeypatch, db)
    e.build_index()

    assert e._index_status == "build_failed"
    assert e._index_built is False
    assert "missing_core" in e._index_degraded_reason
    assert "embedding" in e._index_degraded_reason
    e.vector_index.add.assert_not_called()        # 核心列缺失，不入索引


# ============ 组2: gateway 信号合并 ============

_THRESHOLDS = {"high_threshold": 0.60, "low_threshold": 0.53, "gray_action": "handoff"}


def _patch_gateway(monkeypatch, status, degraded_reason, search_results):
    mock_engine = MagicMock()
    mock_engine.index_health.return_value = {"status": status, "degraded_reason": degraded_reason}
    mock_engine.search.return_value = search_results
    monkeypatch.setattr(retrieval_gateway, "get_hybrid_engine", lambda: mock_engine)
    monkeypatch.setattr(retrieval_gateway, "get_retrieval_thresholds", lambda: dict(_THRESHOLDS))
    return mock_engine


def test_gateway_build_failed_forces_not_found(monkeypatch):
    # 即使有高分结果，build_failed 也强制 not_found（不伪装成正常无答案）
    _patch_gateway(monkeypatch, "build_failed", "chunks_missing_core_columns:['embedding']",
                   [{"id": "c1", "score": 0.95, "content": "x"}])
    r = retrieval_gateway.search_with_confidence("q")
    assert r["status"] == "not_found"
    assert r["action"] == "handoff"
    assert r["degraded"] is True
    assert "missing_core" in r["degraded_reason"]


def test_gateway_schema_degraded_keeps_high(monkeypatch):
    # 【明确点1】schema_degraded + 高分 -> 仍 high，不被降级，只带 degraded 信号
    _patch_gateway(monkeypatch, "schema_degraded", "schema_missing_columns:['product_name']",
                   [{"id": "c1", "score": 0.95, "content": "x"}])
    r = retrieval_gateway.search_with_confidence("q")
    assert r["status"] == "high"
    assert r["degraded"] is True
    assert "product_name" in r["degraded_reason"]


def test_gateway_index_failure_priority_over_reranker(monkeypatch):
    # 【明确点2】同时 index build_failed + reranker degraded -> 报 index 级
    _patch_gateway(monkeypatch, "build_failed", "chunks_missing_core_columns:['embedding']",
                   [{"id": "c1", "score": 0.95, "_degraded": True, "_degraded_reason": "reranker_unavailable"}])
    r = retrieval_gateway.search_with_confidence("q")
    assert r["status"] == "not_found"
    assert r["degraded_reason"] != "reranker_unavailable"
    assert "missing_core" in r["degraded_reason"]


def test_gateway_ok_no_degraded(monkeypatch):
    # ok + 高分 -> high，无降级信号（证明加告警不会误报正常情况）
    _patch_gateway(monkeypatch, "ok", None, [{"id": "c1", "score": 0.95, "content": "x"}])
    r = retrieval_gateway.search_with_confidence("q")
    assert r["status"] == "high"
    assert r["degraded"] is False
    assert r["degraded_reason"] is None


def test_gateway_reranker_degraded_when_index_ok(monkeypatch):
    # index ok + reranker degraded item -> degraded_reason=reranker_unavailable
    _patch_gateway(monkeypatch, "ok", None,
                   [{"id": "c1", "score": 0.95, "_degraded": True, "_degraded_reason": "reranker_unavailable"}])
    r = retrieval_gateway.search_with_confidence("q")
    assert r["degraded"] is True
    assert r["degraded_reason"] == "reranker_unavailable"
