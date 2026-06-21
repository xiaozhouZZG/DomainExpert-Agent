"""SQLite connection and bootstrap helpers."""

from __future__ import annotations

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/platform.db")


def get_db_connection() -> sqlite3.Connection:
    """Return a configured SQLite connection."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_tables() -> None:
    """Create all required tables and apply compatible migrations."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT UNIQUE NOT NULL,
                file_path TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT UNIQUE NOT NULL,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB,
                FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                created_at TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                customer_id TEXT NOT NULL,
                product_id TEXT,
                amount REAL,
                status TEXT,
                created_at TEXT,
                FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id TEXT UNIQUE NOT NULL,
                customer_id TEXT,
                product_id TEXT,
                amount REAL,
                sale_date TEXT,
                created_at TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id TEXT UNIQUE NOT NULL,
                workflow_type TEXT NOT NULL,
                customer_id TEXT,
                order_id TEXT,
                session_id TEXT,
                trace_id TEXT,
                title TEXT,
                content TEXT,
                amount REAL,
                status TEXT,
                created_at TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                category TEXT,
                price REAL,
                stock INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                description TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS kg_triples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'default_user',
                customer_id TEXT,
                title TEXT,
                thread_id TEXT,
                state_snapshot TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                trace_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                agent TEXT,
                engine TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT,
                agent_name TEXT NOT NULL,
                status TEXT,
                duration_ms REAL,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                source TEXT,
                metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS system_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                trace_id TEXT,
                agent TEXT,
                engine TEXT NOT NULL,
                latency_ms REAL,
                kb_hit INTEGER DEFAULT 0,
                countable INTEGER DEFAULT 1,
                trace_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS handoff_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_id TEXT DEFAULT 'default_user',
                customer_id TEXT,
                order_id TEXT,
                question TEXT,
                user_question TEXT,
                agent_response TEXT,
                notes TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS xianyu_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT UNIQUE NOT NULL,
                buyer_name TEXT,
                platform TEXT DEFAULT 'goofish',
                status TEXT DEFAULT 'open',
                last_intent TEXT,
                last_message_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS xianyu_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT UNIQUE NOT NULL,
                conversation_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT,
                draft_reply TEXT,
                approval_id TEXT,
                sent_status TEXT DEFAULT 'pending',
                raw_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES xianyu_conversations(conversation_id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                event_time TEXT NOT NULL,
                component TEXT NOT NULL,
                action_name TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms REAL,
                session_id TEXT,
                trace_id TEXT,
                conversation_id TEXT,
                message_id TEXT,
                approval_id TEXT,
                screenshot_path TEXT,
                data_source TEXT,
                detail TEXT,
                error TEXT,
                metadata_json TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS competitor_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                title TEXT,
                price REAL,
                sold_count TEXT,
                platform TEXT NOT NULL,
                source_label TEXT NOT NULL,
                raw_json TEXT,
                observed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS xianyu_listing_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL,
                price REAL,
                item_url TEXT,
                view_count TEXT,
                want_count TEXT,
                published_at TEXT,
                source_label TEXT NOT NULL,
                raw_json TEXT,
                last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS xianyu_generated_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id TEXT UNIQUE NOT NULL,
                artifact_type TEXT NOT NULL,
                keyword TEXT,
                conversation_id TEXT,
                source_label TEXT NOT NULL,
                summary_text TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()
        _migrate_database(cursor)
        _migrate_dashboard_tables(cursor)
        conn.commit()
        logger.info("database schema ready: %s", DB_PATH)
    except Exception:
        logger.exception("database initialization failed")
        raise
    finally:
        if conn:
            conn.close()


def ensure_db_ready() -> dict[str, object]:
    """Create schema and seed required baseline data."""
    ensure_tables()

    # seed.py已删除（假竞品数据，非运行时必需）
    # from seed import has_required_seed_data, seed_all
    # seeded = False
    # seed_stats: dict[str, int] = {}
    # if not has_required_seed_data():
    #     seed_stats = seed_all()
    #     seeded = True

    return {
        "db_path": DB_PATH,
        "seeded": False,
        "seed_stats": {},
    }


def _migrate_database(cursor: sqlite3.Cursor) -> None:
    """Backfill missing columns for existing databases."""
    try:
        _ensure_columns(
            cursor,
            "chunks",
            {
                "category": "ALTER TABLE chunks ADD COLUMN category TEXT",
                "source": "ALTER TABLE chunks ADD COLUMN source TEXT",
                "created_at": "ALTER TABLE chunks ADD COLUMN created_at TEXT",
                "business_line": "ALTER TABLE chunks ADD COLUMN business_line TEXT",
                "priority": "ALTER TABLE chunks ADD COLUMN priority INTEGER DEFAULT 0",
            },
        )
        cursor.execute("UPDATE chunks SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")

        _ensure_columns(
            cursor,
            "conversation_sessions",
            {
                "user_id": "ALTER TABLE conversation_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default_user'",
                "customer_id": "ALTER TABLE conversation_sessions ADD COLUMN customer_id TEXT",
                "title": "ALTER TABLE conversation_sessions ADD COLUMN title TEXT",
            },
        )

        _ensure_columns(
            cursor,
            "approvals",
            {
                "customer_id": "ALTER TABLE approvals ADD COLUMN customer_id TEXT",
                "order_id": "ALTER TABLE approvals ADD COLUMN order_id TEXT",
                "session_id": "ALTER TABLE approvals ADD COLUMN session_id TEXT",
                "trace_id": "ALTER TABLE approvals ADD COLUMN trace_id TEXT",
            },
        )

        _ensure_columns(
            cursor,
            "conversations",
            {
                "trace_id": "ALTER TABLE conversations ADD COLUMN trace_id TEXT",
                "agent": "ALTER TABLE conversations ADD COLUMN agent TEXT",
                "engine": "ALTER TABLE conversations ADD COLUMN engine TEXT",
            },
        )

        if _table_exists(cursor, "messages"):
            _ensure_columns(
                cursor,
                "messages",
                {
                    "agent": "ALTER TABLE messages ADD COLUMN agent TEXT",
                    "trace_id": "ALTER TABLE messages ADD COLUMN trace_id TEXT",
                    "engine": "ALTER TABLE messages ADD COLUMN engine TEXT",
                    "kb_hit": "ALTER TABLE messages ADD COLUMN kb_hit INTEGER DEFAULT 0",
                    "prompt_tokens": "ALTER TABLE messages ADD COLUMN prompt_tokens INTEGER",
                    "completion_tokens": "ALTER TABLE messages ADD COLUMN completion_tokens INTEGER",
                    "total_tokens": "ALTER TABLE messages ADD COLUMN total_tokens INTEGER",
                    "cost": "ALTER TABLE messages ADD COLUMN cost REAL",
                    "estimated": "ALTER TABLE messages ADD COLUMN estimated INTEGER DEFAULT 1",
                    "latency_ms": "ALTER TABLE messages ADD COLUMN latency_ms REAL",
                    "created_at": "ALTER TABLE messages ADD COLUMN created_at TEXT",
                },
            )
            cursor.execute("UPDATE messages SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")

        _ensure_columns(
            cursor,
            "request_logs",
            {
                "trace_id": "ALTER TABLE request_logs ADD COLUMN trace_id TEXT",
                "trace_data": "ALTER TABLE request_logs ADD COLUMN trace_data TEXT",
                "agent": "ALTER TABLE request_logs ADD COLUMN agent TEXT",
                "kb_hit": "ALTER TABLE request_logs ADD COLUMN kb_hit INTEGER DEFAULT 0",
                "countable": "ALTER TABLE request_logs ADD COLUMN countable INTEGER DEFAULT 1",
            },
        )

        _ensure_columns(
            cursor,
            "handoff_tickets",
            {
                "user_id": "ALTER TABLE handoff_tickets ADD COLUMN user_id TEXT DEFAULT 'default_user'",
                "question": "ALTER TABLE handoff_tickets ADD COLUMN question TEXT",
                "user_question": "ALTER TABLE handoff_tickets ADD COLUMN user_question TEXT",
                "notes": "ALTER TABLE handoff_tickets ADD COLUMN notes TEXT",
                "customer_id": "ALTER TABLE handoff_tickets ADD COLUMN customer_id TEXT",
                "order_id": "ALTER TABLE handoff_tickets ADD COLUMN order_id TEXT",
            },
        )
    except Exception:
        logger.exception("database migration failed")
        raise


def _migrate_dashboard_tables(cursor: sqlite3.Cursor) -> None:
    """Ensure dashboard operational tables keep up with schema changes."""
    try:
        _ensure_columns(
            cursor,
            "execution_logs",
            {
                "approval_id": "ALTER TABLE execution_logs ADD COLUMN approval_id TEXT",
                "screenshot_path": "ALTER TABLE execution_logs ADD COLUMN screenshot_path TEXT",
                "data_source": "ALTER TABLE execution_logs ADD COLUMN data_source TEXT",
                "detail": "ALTER TABLE execution_logs ADD COLUMN detail TEXT",
                "error": "ALTER TABLE execution_logs ADD COLUMN error TEXT",
                "metadata_json": "ALTER TABLE execution_logs ADD COLUMN metadata_json TEXT",
            },
        )

        _ensure_columns(
            cursor,
            "competitor_observations",
            {
                "source_label": "ALTER TABLE competitor_observations ADD COLUMN source_label TEXT",
                "raw_json": "ALTER TABLE competitor_observations ADD COLUMN raw_json TEXT",
                "observed_at": "ALTER TABLE competitor_observations ADD COLUMN observed_at TEXT",
            },
        )
        cursor.execute(
            "UPDATE competitor_observations SET source_label = platform WHERE source_label IS NULL"
        )
        cursor.execute(
            "UPDATE competitor_observations SET observed_at = CURRENT_TIMESTAMP WHERE observed_at IS NULL"
        )

        _ensure_columns(
            cursor,
            "xianyu_listing_snapshots",
            {
                "source_label": "ALTER TABLE xianyu_listing_snapshots ADD COLUMN source_label TEXT",
                "raw_json": "ALTER TABLE xianyu_listing_snapshots ADD COLUMN raw_json TEXT",
                "last_seen_at": "ALTER TABLE xianyu_listing_snapshots ADD COLUMN last_seen_at TEXT",
                "item_url": "ALTER TABLE xianyu_listing_snapshots ADD COLUMN item_url TEXT",
                "view_count": "ALTER TABLE xianyu_listing_snapshots ADD COLUMN view_count TEXT",
                "want_count": "ALTER TABLE xianyu_listing_snapshots ADD COLUMN want_count TEXT",
                "published_at": "ALTER TABLE xianyu_listing_snapshots ADD COLUMN published_at TEXT",
            },
        )
        cursor.execute(
            "UPDATE xianyu_listing_snapshots SET last_seen_at = CURRENT_TIMESTAMP WHERE last_seen_at IS NULL"
        )

        _ensure_columns(
            cursor,
            "xianyu_generated_artifacts",
            {
                "keyword": "ALTER TABLE xianyu_generated_artifacts ADD COLUMN keyword TEXT",
                "conversation_id": "ALTER TABLE xianyu_generated_artifacts ADD COLUMN conversation_id TEXT",
                "source_label": "ALTER TABLE xianyu_generated_artifacts ADD COLUMN source_label TEXT",
                "summary_text": "ALTER TABLE xianyu_generated_artifacts ADD COLUMN summary_text TEXT",
                "payload_json": "ALTER TABLE xianyu_generated_artifacts ADD COLUMN payload_json TEXT",
                "created_at": "ALTER TABLE xianyu_generated_artifacts ADD COLUMN created_at TEXT",
            },
        )
        cursor.execute(
            "UPDATE xianyu_generated_artifacts SET source_label = 'real' WHERE source_label IS NULL"
        )
        cursor.execute(
            "UPDATE xianyu_generated_artifacts SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )
    except Exception:
        logger.exception("dashboard tables migration failed")
        raise


def _table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    row = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _ensure_columns(
    cursor: sqlite3.Cursor,
    table_name: str,
    statements: dict[str, str],
) -> None:
    if not _table_exists(cursor, table_name):
        return

    cursor.execute(f"PRAGMA table_info({table_name})")
    existing = {row[1] for row in cursor.fetchall()}
    for column_name, statement in statements.items():
        if column_name not in existing:
            logger.info("migrating table %s: add column %s", table_name, column_name)
            cursor.execute(statement)
