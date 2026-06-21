"""Database seed utilities for enterprise customer-service demo data.

These seed helpers are used by startup bootstrap code after schema creation.
They are intentionally idempotent so a fresh clone and an existing workspace
can both run the same bootstrap path safely.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Iterable

from database.connection import get_db_connection

logger = logging.getLogger(__name__)


def seed_all() -> dict[str, int]:
    """Seed the minimum business data required by the app."""
    stats = {
        "customers": seed_customers(),
        "products": seed_products(),
        "orders": seed_orders(),
        "sales": seed_sales(),
        "kg_triples": seed_kg_triples(),
        "documents": seed_knowledge_base(),
    }
    logger.info("database seed complete: %s", stats)
    return stats


def seed_customers() -> int:
    customers = [
        ("CUST001", "张三", "13800138000", "zhangsan@example.com"),
        ("CUST002", "李四", "13900139000", "lisi@example.com"),
        ("CUST003", "王五", "13700137000", "wangwu@example.com"),
    ]
    now = datetime.now().isoformat()
    return _executemany_count(
        """
        INSERT OR IGNORE INTO customers (customer_id, name, phone, email, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(customer_id, name, phone, email, now) for customer_id, name, phone, email in customers],
    )


def seed_products() -> int:
    products = [
        ("SKU001", "iPhone 15 Pro Max 256GB", "手机", 9999.0, 50, "active", "苹果旗舰机型"),
        ("SKU002", "MacBook Pro 14 M3", "电脑", 15999.0, 20, "active", "专业创作笔记本"),
        ("SKU003", "AirPods Pro 2", "耳机", 1899.0, 3, "active", "主动降噪耳机"),
        ("SKU004", "iPad Air 128GB", "平板", 4799.0, 0, "out_of_stock", "当前缺货"),
        ("SKU005", "小米 13 Ultra 16+512GB", "手机", 6999.0, 100, "active", "影像旗舰"),
        ("SKU006", "华为 Mate 60 Pro", "手机", 6999.0, 2, "active", "库存紧张"),
        ("SKU007", "Dell XPS 15", "电脑", 12999.0, 15, "active", "高性能轻薄本"),
        ("SKU008", "Sony WH-1000XM5", "耳机", 2599.0, 0, "out_of_stock", "售罄待补货"),
    ]
    now = datetime.now().isoformat()
    return _executemany_count(
        """
        INSERT OR IGNORE INTO products
        (sku, name, category, price, stock, status, description, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(sku, name, category, price, stock, status, description, now) for sku, name, category, price, stock, status, description in products],
    )


def seed_orders() -> int:
    base_time = datetime.now() - timedelta(days=5)
    orders = [
        ("202606010001", "CUST001", "SKU001", 9999.0, "completed", base_time.isoformat()),
        ("202606010002", "CUST001", "SKU003", 1899.0, "shipped", (base_time + timedelta(days=1)).isoformat()),
        ("202606010003", "CUST002", "SKU005", 6999.0, "paid", (base_time + timedelta(days=2)).isoformat()),
        ("202606010004", "CUST003", "SKU006", 6999.0, "completed", (base_time + timedelta(days=3)).isoformat()),
    ]
    return _executemany_count(
        """
        INSERT OR IGNORE INTO orders
        (order_id, customer_id, product_id, amount, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        orders,
    )


def seed_sales() -> int:
    base_date = datetime.now() - timedelta(days=30)
    sales_rows = []
    for index in range(20):
        sale_date = (base_date + timedelta(days=index)).strftime("%Y-%m-%d")
        sku = f"SKU{((index % 8) + 1):03d}"
        sales_rows.append(
            (
                f"SALE{index + 1:03d}",
                f"CUST{((index % 3) + 1):03d}",
                sku,
                round(100 + index * 50.5, 2),
                sale_date,
                datetime.now().isoformat(),
            )
        )
    return _executemany_count(
        """
        INSERT OR IGNORE INTO sales
        (sale_id, customer_id, product_id, amount, sale_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        sales_rows,
    )


def seed_kg_triples() -> int:
    triples = [
        ("iPhone 15", "属于", "苹果产品", 1.0, "产品知识库"),
        ("iPhone 15", "支持", "5G网络", 1.0, "产品知识库"),
        ("退货政策", "规定", "7天无理由退货", 1.0, "服务政策"),
        ("退货政策", "要求", "商品完好未使用", 1.0, "服务政策"),
        ("会员等级", "包含", "普通会员", 1.0, "会员体系"),
        ("会员等级", "包含", "黄金会员", 1.0, "会员体系"),
    ]
    now = datetime.now().isoformat()
    return _executemany_count(
        """
        INSERT OR IGNORE INTO kg_triples
        (subject, predicate, object, confidence, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(subject, predicate, obj, confidence, source, now) for subject, predicate, obj, confidence, source in triples],
    )


def seed_knowledge_base() -> int:
    content = """
退货政策说明

一、退货条件
1. 商品在收到后 7 天内可申请退货。
2. 商品必须保持完好，未使用、未损坏。
3. 商品原包装、配件、说明书等需齐全。
4. 个性化定制商品不支持退货。

二、退货流程
1. 登录账户并进入订单详情。
2. 点击“申请退货”。
3. 选择退货原因并上传凭证。
4. 等待客服审核，审核通过后寄回商品。
5. 验收合格后 3-5 个工作日原路退款。

会员等级说明

1. 普通会员：注册即可获得。
2. 黄金会员：累计消费满 1000 元。
3. 钻石会员：累计消费满 5000 元。
    """.strip()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        existing = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()[0] or 0
        if existing:
            return 0

        doc_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        cursor.execute(
            """
            INSERT INTO documents (doc_id, file_path, title, created_at, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                "knowledge_base.txt",
                "售后与会员政策",
                now,
                json.dumps({"type": "policy", "version": "1.0"}, ensure_ascii=False),
            ),
        )
        paragraphs = [paragraph.strip() for paragraph in content.split("\n\n") if paragraph.strip()]
        for index, paragraph in enumerate(paragraphs):
            cursor.execute(
                """
                INSERT INTO chunks
                (chunk_id, doc_id, chunk_index, text, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"{doc_id}_chunk_{index}", doc_id, index, paragraph, None, now),
            )
        conn.commit()
        return 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def has_required_seed_data() -> bool:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        checks = {
            "customers": "SELECT COUNT(*) FROM customers",
            "products": "SELECT COUNT(*) FROM products",
            "orders": "SELECT COUNT(*) FROM orders",
        }
        for name, query in checks.items():
            count = cursor.execute(query).fetchone()[0] or 0
            if count == 0:
                logger.info("seed data missing: %s", name)
                return False
        return True
    finally:
        conn.close()


def _executemany_count(sql: str, rows: Iterable[tuple]) -> int:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        before = conn.total_changes
        cursor.executemany(sql, list(rows))
        conn.commit()
        return conn.total_changes - before
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    result = seed_all()
    print(json.dumps(result, ensure_ascii=False, indent=2))
