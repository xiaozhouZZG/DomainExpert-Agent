"""数据库连接管理模块"""
import sqlite3
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 数据库路径
DB_PATH = os.getenv("DB_PATH", "data/platform.db")


def get_db_connection() -> sqlite3.Connection:
    """
    获取数据库连接

    配置:
    - WAL 模式（高并发读）
    - 外键约束开启
    - 超时 30秒

    返回:
        sqlite3.Connection
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)

    # 启用 WAL 模式（读多写少场景优化）
    conn.execute("PRAGMA journal_mode=WAL")

    # 启用外键约束
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


def ensure_tables():
    """
    确保所有表存在（兜底检查）

    注意:
    - 正式初始化应使用 database/models.py 的 init_database()
    - 这里提供最小 schema，防止模块单独使用时崩溃
    - 字段必须和 models.py 完全一致
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 文档表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT UNIQUE NOT NULL,
                file_path TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata TEXT
            )
        """)

        # 分块表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT UNIQUE NOT NULL,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB,
                FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
            )
        """)

        # 客户表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                created_at TEXT
            )
        """)

        # 订单表
        cursor.execute("""
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
        """)

        # 销售表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id TEXT UNIQUE NOT NULL,
                customer_id TEXT,
                product_id TEXT,
                amount REAL,
                sale_date TEXT,
                created_at TEXT
            )
        """)

        # 审批表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id TEXT UNIQUE NOT NULL,
                workflow_type TEXT NOT NULL,
                title TEXT,
                content TEXT,
                amount REAL,
                status TEXT,
                created_at TEXT
            )
        """)

        # 知识图谱三元组表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kg_triples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 对话会话表
        cursor.execute("""
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
        """)

        # 兼容旧表：检查并添加缺失的字段
        cursor.execute("PRAGMA table_info(conversation_sessions)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'user_id' not in columns:
            cursor.execute("ALTER TABLE conversation_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default_user'")
            logger.info("✓ 补全字段: conversation_sessions.user_id")

        if 'customer_id' not in columns:
            cursor.execute("ALTER TABLE conversation_sessions ADD COLUMN customer_id TEXT")
            logger.info("✓ 补全字段: conversation_sessions.customer_id")

        if 'title' not in columns:
            cursor.execute("ALTER TABLE conversation_sessions ADD COLUMN title TEXT")
            logger.info("✓ 补全字段: conversation_sessions.title")

        # 对话记录表（新增）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                trace_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                engine TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Agent 执行记录表（新增）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT,
                agent_name TEXT NOT NULL,
                status TEXT,
                duration_ms REAL,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 知识库表（新增，兼容 RAG 模块）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                source TEXT,
                metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 系统配置表（新增，存储 LLM 配置等）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 请求日志表（新增，记录每次请求统计 + 详细执行轨迹）
        cursor.execute("""
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
        """)

        # 转人工工单表（新增，记录未命中转人工的工单）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS handoff_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_question TEXT NOT NULL,
                agent_response TEXT,
                status TEXT DEFAULT '待处理',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

        # 数据库迁移：检查并添加缺失的字段
        _migrate_database(cursor)

        conn.commit()
        logger.info("数据库表检查完成")

    except Exception as e:
        logger.error(f"数据库表初始化失败: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()


def _migrate_database(cursor):
    """
    数据库平滑迁移：检查所有表并添加缺失的字段

    覆盖所有新增过的列，确保旧库平滑升级
    """
    try:
        # ============ chunks 表（新增元数据字段用于过滤）============
        cursor.execute("PRAGMA table_info(chunks)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'category' not in columns:
            logger.info("迁移: chunks 添加 category（文档分类）")
            cursor.execute("ALTER TABLE chunks ADD COLUMN category TEXT")

        if 'source' not in columns:
            logger.info("迁移: chunks 添加 source（来源）")
            cursor.execute("ALTER TABLE chunks ADD COLUMN source TEXT")

        if 'created_at' not in columns:
            logger.info("迁移: chunks 添加 created_at（创建时间）")
            cursor.execute("ALTER TABLE chunks ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        if 'business_line' not in columns:
            logger.info("迁移: chunks 添加 business_line（业务线）")
            cursor.execute("ALTER TABLE chunks ADD COLUMN business_line TEXT")

        if 'priority' not in columns:
            logger.info("迁移: chunks 添加 priority（优先级）")
            cursor.execute("ALTER TABLE chunks ADD COLUMN priority INTEGER DEFAULT 0")

        # ============ conversation_sessions 表 ============
        cursor.execute("PRAGMA table_info(conversation_sessions)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'user_id' not in columns:
            logger.info("迁移: conversation_sessions 添加 user_id")
            cursor.execute("ALTER TABLE conversation_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default_user'")

        if 'title' not in columns:
            logger.info("迁移: conversation_sessions 添加 title")
            cursor.execute("ALTER TABLE conversation_sessions ADD COLUMN title TEXT")

        # ============ conversations 表 ============
        cursor.execute("PRAGMA table_info(conversations)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'trace_id' not in columns:
            logger.info("迁移: conversations 添加 trace_id")
            cursor.execute("ALTER TABLE conversations ADD COLUMN trace_id TEXT")

        if 'agent' not in columns:
            logger.info("迁移: conversations 添加 agent")
            cursor.execute("ALTER TABLE conversations ADD COLUMN agent TEXT")

        if 'engine' not in columns:
            logger.info("迁移: conversations 添加 engine")
            cursor.execute("ALTER TABLE conversations ADD COLUMN engine TEXT")

        # ============ messages 表（如果存在）============
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(messages)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'agent' not in columns:
                logger.info("迁移: messages 添加 agent")
                cursor.execute("ALTER TABLE messages ADD COLUMN agent TEXT")

            if 'trace_id' not in columns:
                logger.info("迁移: messages 添加 trace_id")
                cursor.execute("ALTER TABLE messages ADD COLUMN trace_id TEXT")

            if 'engine' not in columns:
                logger.info("迁移: messages 添加 engine")
                cursor.execute("ALTER TABLE messages ADD COLUMN engine TEXT")

            if 'kb_hit' not in columns:
                logger.info("迁移: messages 添加 kb_hit")
                cursor.execute("ALTER TABLE messages ADD COLUMN kb_hit INTEGER DEFAULT 0")

            if 'prompt_tokens' not in columns:
                logger.info("迁移: messages 添加 prompt_tokens")
                cursor.execute("ALTER TABLE messages ADD COLUMN prompt_tokens INTEGER")

            if 'completion_tokens' not in columns:
                logger.info("迁移: messages 添加 completion_tokens")
                cursor.execute("ALTER TABLE messages ADD COLUMN completion_tokens INTEGER")

            if 'total_tokens' not in columns:
                logger.info("迁移: messages 添加 total_tokens")
                cursor.execute("ALTER TABLE messages ADD COLUMN total_tokens INTEGER")

            if 'cost' not in columns:
                logger.info("迁移: messages 添加 cost")
                cursor.execute("ALTER TABLE messages ADD COLUMN cost REAL")

            if 'estimated' not in columns:
                logger.info("迁移: messages 添加 estimated")
                cursor.execute("ALTER TABLE messages ADD COLUMN estimated INTEGER DEFAULT 1")

            if 'latency_ms' not in columns:
                logger.info("迁移: messages 添加 latency_ms")
                cursor.execute("ALTER TABLE messages ADD COLUMN latency_ms REAL")

            if 'created_at' not in columns:
                logger.info("迁移: messages 添加 created_at")
                cursor.execute("ALTER TABLE messages ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

        # ============ request_logs 表 ============
        cursor.execute("PRAGMA table_info(request_logs)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'trace_id' not in columns:
            logger.info("迁移: request_logs 添加 trace_id")
            cursor.execute("ALTER TABLE request_logs ADD COLUMN trace_id TEXT")

        if 'trace_data' not in columns:
            logger.info("迁移: request_logs 添加 trace_data")
            cursor.execute("ALTER TABLE request_logs ADD COLUMN trace_data TEXT")

        if 'agent' not in columns:
            logger.info("迁移: request_logs 添加 agent")
            cursor.execute("ALTER TABLE request_logs ADD COLUMN agent TEXT")

        if 'kb_hit' not in columns:
            logger.info("迁移: request_logs 添加 kb_hit")
            cursor.execute("ALTER TABLE request_logs ADD COLUMN kb_hit INTEGER DEFAULT 0")

        if 'countable' not in columns:
            logger.info("迁移: request_logs 添加 countable")
            cursor.execute("ALTER TABLE request_logs ADD COLUMN countable INTEGER DEFAULT 1")

        # ============ handoff_tickets 表 ============
        cursor.execute("PRAGMA table_info(handoff_tickets)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'user_id' not in columns:
            logger.info("迁移: handoff_tickets 添加 user_id")
            cursor.execute("ALTER TABLE handoff_tickets ADD COLUMN user_id TEXT DEFAULT 'default_user'")

        if 'question' not in columns:
            logger.info("迁移: handoff_tickets 添加 question")
            cursor.execute("ALTER TABLE handoff_tickets ADD COLUMN question TEXT")

        if 'user_question' not in columns:
            logger.info("迁移: handoff_tickets 添加 user_question")
            cursor.execute("ALTER TABLE handoff_tickets ADD COLUMN user_question TEXT")

        logger.info("数据库迁移完成")

    except Exception as e:
        logger.warning(f"数据库迁移失败（可能表不存在）: {str(e)}")
