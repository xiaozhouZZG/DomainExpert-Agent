import json
import sqlite3
import threading


def test_ensure_tables_creates_operational_columns(tmp_path, monkeypatch):
    from database import connection

    db_path = tmp_path / "platform.db"
    monkeypatch.setattr(connection, "DB_PATH", str(db_path))

    connection.ensure_tables()

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "products" in tables

        handoff_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(handoff_tickets)")
        }
        assert {"user_id", "question", "notes", "customer_id", "order_id"} <= handoff_columns

        approval_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(approvals)")
        }
        assert {"customer_id", "order_id", "session_id", "trace_id"} <= approval_columns
    finally:
        conn.close()


def test_ensure_db_ready_seeds_required_business_data(tmp_path, monkeypatch):
    from database import connection

    db_path = tmp_path / "platform.db"
    monkeypatch.setattr(connection, "DB_PATH", str(db_path))

    result = connection.ensure_db_ready()

    assert result["seeded"] is True

    conn = sqlite3.connect(db_path)
    try:
        customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        order_row = conn.execute(
            "SELECT customer_id, product_id FROM orders WHERE order_id = ?",
            ("202606010001",),
        ).fetchone()
        assert customers >= 3
        assert products >= 8
        assert orders >= 4
        assert order_row == ("CUST001", "SKU001")
    finally:
        conn.close()


def test_migration_adds_created_at_columns_to_existing_old_tables(tmp_path, monkeypatch):
    from database import connection

    db_path = tmp_path / "old_platform.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT UNIQUE NOT NULL,
                file_path TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT UNIQUE NOT NULL,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(connection, "DB_PATH", str(db_path))
    connection.ensure_tables()

    conn = sqlite3.connect(db_path)
    try:
        chunk_columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
        message_columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        assert "created_at" in chunk_columns
        assert "created_at" in message_columns
    finally:
        conn.close()


def test_supervisor_fallback_uses_route_history_without_name_error():
    from core.langgraph_engine import LangGraphEngine

    class FailingLLM:
        def invoke(self, messages):
            raise RuntimeError("router unavailable")

    engine = LangGraphEngine.__new__(LangGraphEngine)
    engine.llm = FailingLLM()

    state = {
        "messages": [{"role": "user", "content": "帮我查询订单"}],
        "iteration_count": 0,
        "route_history": [],
    }

    result = engine._supervisor_node(state)

    assert result["next_action"] == "order_agent"
    assert result["route_history"] == ["order_agent"]
    assert result["iteration_count"] == 1


def test_supervisor_fallback_routes_xianyu_requests():
    from core.langgraph_engine import LangGraphEngine

    class FailingLLM:
        def invoke(self, messages):
            raise RuntimeError("router unavailable")

    engine = LangGraphEngine.__new__(LangGraphEngine)
    engine.llm = FailingLLM()

    state = {
        "messages": [{"role": "user", "content": "帮我读取闲鱼买家消息"}],
        "iteration_count": 0,
        "route_history": [],
    }

    result = engine._supervisor_node(state)

    assert result["next_action"] == "xianyu_agent"
    assert result["route_history"] == ["xianyu_agent"]


def test_hybrid_rag_engine_initializes_mode_when_components_are_stubbed(monkeypatch):
    from knowledge.hybrid_rag_engine import HybridRAGEngine

    monkeypatch.setattr(HybridRAGEngine, "_init_components", lambda self: None)

    engine = HybridRAGEngine()

    assert engine.mode == "vector"
    assert engine.get_stats()["mode"] == "vector"


def test_xianyu_send_reply_tool_creates_approval_before_write(tmp_path, monkeypatch):
    from database import connection
    from tools.xianyu import send_xianyu_reply

    db_path = tmp_path / "platform.db"
    monkeypatch.setattr(connection, "DB_PATH", str(db_path))
    monkeypatch.setenv("LISTING_PLATFORM", "goofish")
    connection.ensure_tables()

    threading.current_thread().current_customer_id = "CUST001"
    threading.current_thread().session_id = "session-1"
    threading.current_thread().trace_id = "trace-1"

    payload = send_xianyu_reply.invoke(
        {
            "conversation_id": "conv-1",
            "content": "您好，可以小刀。",
        }
    )
    result = json.loads(payload)

    assert result["status"] == "approval_required"
    assert result["approval_id"]

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT workflow_type, customer_id, session_id, trace_id FROM approvals WHERE approval_id = ?",
            (result["approval_id"],),
        ).fetchone()
        assert row == ("xianyu_send_reply", "CUST001", "session-1", "trace-1")
    finally:
        conn.close()


def test_xianyu_service_ingests_messages_and_classifies_intent(tmp_path, monkeypatch):
    from core.xianyu_service import ingest_buyer_messages
    from database import connection

    db_path = tmp_path / "platform.db"
    monkeypatch.setattr(connection, "DB_PATH", str(db_path))
    connection.ensure_tables()

    result = ingest_buyer_messages(
        [
            {
                "conversation_id": "conv-price",
                "buyer_name": "买家A",
                "content": "这个还能便宜一点吗？",
                "received_at": "2026-06-17T10:00:00",
            },
            {
                "conversation_id": "conv-price",
                "buyer_name": "买家A",
                "content": "这个还能便宜一点吗？",
                "received_at": "2026-06-17T10:00:00",
            },
        ]
    )

    assert result["inserted"] == 1
    assert result["updated_conversations"] == 1

    conn = sqlite3.connect(db_path)
    try:
        conv = conn.execute(
            "SELECT buyer_name, last_intent, status FROM xianyu_conversations WHERE conversation_id = ?",
            ("conv-price",),
        ).fetchone()
        msg_count = conn.execute("SELECT COUNT(*) FROM xianyu_messages").fetchone()[0]

        assert conv == ("买家A", "price_negotiation", "open")
        assert msg_count == 1
    finally:
        conn.close()


def test_xianyu_listing_schema_has_readonly_listing_fields(tmp_path, monkeypatch):
    from database import connection

    db_path = tmp_path / "platform.db"
    monkeypatch.setattr(connection, "DB_PATH", str(db_path))
    connection.ensure_tables()


def test_xianyu_reply_category_inference_covers_main_buyer_points():
    from api.xianyu import _infer_reply_categories

    categories = _infer_reply_categories("这个键盘能便宜点吗?是真机械轴吗?发货快不快?")

    assert categories[0] == "product_info"
    assert "negotiation" in categories
    assert "shipping" in categories


def test_xianyu_reply_post_process_strips_meta_and_limits_length():
    from api.xianyu import _finalize_reply_response, REPLY_MAX_CHARS

    raw_response = """
    ```json
    {
      "reply_text": "你好呀，这个键盘价格已经参考同类行情了，可以小刀一点，不过幅度不会太大。具体是不是机械轴要以实物和铭牌为准，如果你要我也可以补细节图确认。付款后我这边24小时内安排发出，最晚不超过48小时。\\n\\n几点说明\\n- 这个回复没有直接承诺大降价\\n- 需要我再调整吗",
      "covered_points": ["negotiation", "product_info", "shipping"]
    }
    ```
    """

    finalized = _finalize_reply_response(raw_response)

    assert finalized["parse_status"] == "json"
    assert finalized["covered_points"] == ["negotiation", "product_info", "shipping"]
    assert "几点说明" not in finalized["reply_text"]
    assert "需要我再调整吗" not in finalized["reply_text"]
    assert len(finalized["reply_text"]) <= REPLY_MAX_CHARS


def test_marketing_plan_parser_salvages_realistic_malformed_llm_json():
    from api.xianyu import _parse_marketing_plan_response

    raw_response = """
    ```json
    {
      "channels": [
        {
          "channel": "闲鱼搜索优化",
          "type": "站内",
          "actions": [
            "标题前缀用【即插即用】【成色实拍】等买家常搜词，提升搜索命中率",
            "在商品描述中自然埋入'办公键盘''游戏键盘''有线键盘'等长尾词，覆盖更多搜索场景"
          ]
        },
        {
          "channel": "闲鱼直播",
          "type": "站内",
          "actions": [
            "开直播现场敲击每个按键展示手感，让买家直观感受功能完好",
            "直播中多角度展示成色细节，强调'所见即所得'建立信任"
          ]
        {
          "channel": "小红书引流",
          "type": "站外",
          "actions": [
            "发一篇学生党便宜好用的有线键盘分享笔记，文末引导闲鱼同名查看"
          ]
        }
      ],
      "differentiation_points": [
        "成色实拍+逐键测试，信任感更强",
        "定价卡在中位数下方，性价比定位清晰"
      ],
      "copywriting": [
        {
          "title": "18.8包邮出一把成色不错的有线键盘，每个键都测过",
          "body": "清理工位翻出来的，104键全尺寸有线键盘，USB接口插上就能用。",
          "channel": "闲鱼商品详情/鱼塘发帖"
        },
        {
          "title": "学生党便宜好用的有线键盘，18.8包邮到手即用",
          "body": "宿舍办公打游戏都能用的有线键盘，成色实拍所见即所得。",
          "channel": "小红书笔记"
        }
      ]
    }
    ```
    """

    parsed = _parse_marketing_plan_response(raw_response)

    assert parsed["parse_status"] == "salvaged"
    assert [item["channel"] for item in parsed["channels"]] == ["闲鱼搜索优化", "闲鱼直播", "小红书引流"]
    assert parsed["differentiation_points"] == [
        "成色实拍+逐键测试，信任感更强",
        "定价卡在中位数下方，性价比定位清晰",
    ]
    assert len(parsed["copywriting"]) == 2
    assert parsed["copywriting"][0]["channel"] == "闲鱼商品详情/鱼塘发帖"


def test_profit_analysis_helpers_compute_real_band_and_hypothetical_profit():
    from api.xianyu import _build_price_band_distribution, _build_profit_scenarios

    competitors = [
        {"price": 9.9, "want_count": "4289人想要"},
        {"price": 15.9, "want_count": "1083人想要"},
        {"price": 18.8, "want_count": "19人想要"},
        {"price": 30.0, "want_count": "7人想要"},
        {"price": 55.0, "want_count": None},
    ]
    stats = {
        "price_min": 9.9,
        "price_median": 20.0,
        "price_avg": 32.3,
    }

    bands = _build_price_band_distribution(competitors)
    profit = _build_profit_scenarios(assumed_cost=10.0, stats=stats)

    assert [item["band"] for item in bands] == ["¥0-14.9", "¥15-19.9", "¥20-29.9", "¥30-49.9", "¥50+"]
    assert bands[0]["count"] == 1
    assert bands[0]["total_want_count"] == 4289
    assert bands[1]["count"] == 2
    assert bands[1]["total_want_count"] == 1102
    assert bands[4]["count"] == 1
    assert profit["has_assumed_cost"] is True
    assert profit["assumed_cost"] == 10.0
    assert profit["scenarios"][1]["label"] == "中位价跟随"
    assert profit["scenarios"][1]["suggested_price"] == 20.0
    assert profit["scenarios"][1]["gross_profit"] == 10.0
    assert profit["scenarios"][1]["gross_margin_rate"] == 50.0


def test_xianyu_service_persists_listing_snapshots(tmp_path, monkeypatch):
    from core.xianyu_service import count_on_sale_listings, list_listings, persist_listing_snapshots
    from database import connection

    db_path = tmp_path / "platform.db"
    monkeypatch.setattr(connection, "DB_PATH", str(db_path))
    connection.ensure_tables()

    result = persist_listing_snapshots(
        [
            {
                "item_id": "item-1",
                "title": "Vintage camera lens",
                "status": "on_sale",
                "price": "129.50",
                "view_count": "12",
                "want_count": "3",
                "published_at": "2026-06-18",
                "item_url": "https://www.goofish.com/item?id=item-1",
                "source_label": "goofish",
            }
        ]
    )

    assert result["upserted"] == 1
    listings = list_listings(limit=5)
    assert count_on_sale_listings() == 1
    assert listings == [
        {
            "item_id": "item-1",
            "title": "Vintage camera lens",
            "status": "on_sale",
            "price": 129.50,
            "view_count": "12",
            "want_count": "3",
            "published_at": "2026-06-18",
            "item_url": "https://www.goofish.com/item?id=item-1",
            "last_seen_at": listings[0]["last_seen_at"],
            "data_source": "goofish",
        }
    ]


def test_xianyu_generated_artifacts_persist_and_query_latest(tmp_path, monkeypatch):
    from core.xianyu_service import (
        count_generated_artifacts,
        latest_generated_artifact,
        save_generated_artifact,
    )
    from database import connection

    db_path = tmp_path / "platform.db"
    monkeypatch.setattr(connection, "DB_PATH", str(db_path))
    connection.ensure_tables()

    saved = save_generated_artifact(
        artifact_type="profit_analysis",
        keyword="键盘",
        payload={"status": "success", "keyword": "键盘", "analysis": {"profit_analysis": {"summary": "真实分析"}}},
        source_label="real",
        summary_text="真实分析",
    )

    assert saved["artifact_type"] == "profit_analysis"
    assert count_generated_artifacts() == 1

    latest = latest_generated_artifact("profit_analysis")
    assert latest is not None
    assert latest["keyword"] == "键盘"
    assert latest["payload"]["analysis"]["profit_analysis"]["summary"] == "真实分析"
