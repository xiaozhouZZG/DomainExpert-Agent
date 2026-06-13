"""种子数据生成脚本"""
import sys
import logging
from datetime import datetime, timedelta
import uuid
import json

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# 导入数据库模块
from database.models import init_database
from database.connection import get_db_connection


def seed_customers():
    """插入客户种子数据"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        customers = [
            ("CUST001", "张三", "13800138000", "zhangsan@example.com"),
            ("CUST002", "李四", "13900139000", "lisi@example.com"),
            ("CUST003", "黄金", "13700137000", "huangjin@example.com"),
        ]

        now = datetime.now().isoformat()

        for cust_id, name, phone, email in customers:
            cursor.execute("""
                INSERT OR IGNORE INTO customers (customer_id, name, phone, email, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (cust_id, name, phone, email, now))

        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM customers")
        count = cursor.fetchone()[0]
        logger.info(f"✓ 客户表: {count} 条记录")

    except Exception as e:
        logger.error(f"插入客户数据失败: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()


def seed_orders():
    """插入订单种子数据"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        orders = [
            ("ORD001", "CUST001", "PROD001", 299.99, "completed"),
            ("ORD002", "CUST001", "PROD002", 599.99, "shipped"),
            ("ORD003", "CUST002", "PROD001", 299.99, "paid"),
            ("ORD004", "CUST003", "PROD003", 999.99, "completed"),
        ]

        base_time = datetime.now() - timedelta(days=5)

        for i, (order_id, cust_id, prod_id, amount, status) in enumerate(orders):
            created_at = (base_time + timedelta(days=i)).isoformat()
            cursor.execute("""
                INSERT OR IGNORE INTO orders (order_id, customer_id, product_id, amount, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (order_id, cust_id, prod_id, amount, status, created_at))

        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM orders")
        count = cursor.fetchone()[0]
        logger.info(f"✓ 订单表: {count} 条记录")

    except Exception as e:
        logger.error(f"插入订单数据失败: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()


def seed_sales():
    """插入销售记录种子数据"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        base_date = datetime.now() - timedelta(days=30)

        sales = []
        for i in range(20):
            sale_date = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
            sales.append((
                f"SALE{i+1:03d}",
                f"CUST{(i % 3) + 1:03d}",
                f"PROD{(i % 5) + 1:03d}",
                round(100 + i * 50.5, 2),
                sale_date,
            ))

        now = datetime.now().isoformat()

        for sale_id, cust_id, prod_id, amount, sale_date in sales:
            cursor.execute("""
                INSERT OR IGNORE INTO sales (sale_id, customer_id, product_id, amount, sale_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (sale_id, cust_id, prod_id, amount, sale_date, now))

        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM sales")
        count = cursor.fetchone()[0]
        logger.info(f"✓ 销售表: {count} 条记录")

    except Exception as e:
        logger.error(f"插入销售数据失败: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()


def seed_kg_triples():
    """插入知识图谱三元组种子数据"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        triples = [
            ("iPhone 15", "属于", "苹果产品", 1.0, "产品知识库"),
            ("iPhone 15", "支持", "5G网络", 1.0, "产品知识库"),
            ("退货政策", "规定", "7天无理由退货", 1.0, "服务政策"),
            ("退货政策", "要求", "商品完好未使用", 1.0, "服务政策"),
            ("会员等级", "包含", "普通会员", 1.0, "会员体系"),
            ("会员等级", "包含", "黄金会员", 1.0, "会员体系"),
        ]

        now = datetime.now().isoformat()

        for subject, predicate, obj, confidence, source in triples:
            cursor.execute("""
                INSERT OR IGNORE INTO kg_triples (subject, predicate, object, confidence, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (subject, predicate, obj, confidence, source, now))

        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM kg_triples")
        count = cursor.fetchone()[0]
        logger.info(f"✓ 知识图谱: {count} 条记录")

    except Exception as e:
        logger.error(f"插入知识图谱数据失败: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()


def seed_knowledge_base():
    """插入知识库文档和分块（关键词模式可检索）"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 文档内容
        doc_content = """
退货政策说明

一、退货条件
1. 商品在收到后7天内可申请退货
2. 商品必须保持完好，未使用、未损坏
3. 商品原包装、配件、说明书等必须齐全
4. 个人定制商品不支持退货

二、退货流程
1. 登录账户，进入订单详情
2. 点击"申请退货"按钮
3. 选择退货原因，上传照片（如有质量问题）
4. 等待客服审核，审核通过后寄回商品
5. 商品验收合格后，3-5个工作日退款到账

三、注意事项
- 退货运费由买家承担，质量问题除外
- 退款金额为实际支付金额，不含运费
- 使用优惠券购买的商品，退款时不退还优惠券

换货政策

一、换货条件
1. 商品存在质量问题
2. 商品与描述不符
3. 收到错误商品

二、换货流程
与退货流程类似，但需选择"申请换货"并指定换货商品。

会员等级说明

一、会员等级
1. 普通会员：注册即可获得
2. 黄金会员：累计消费满1000元
3. 钻石会员：累计消费满5000元

二、会员权益
- 普通会员：积分返还1%
- 黄金会员：积分返还2%，生日专属优惠
- 钻石会员：积分返还3%，优先客服，专属活动
        """

        doc_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        # 插入文档
        cursor.execute("""
            INSERT OR IGNORE INTO documents (doc_id, file_path, title, created_at, metadata)
            VALUES (?, ?, ?, ?, ?)
        """, (doc_id, "knowledge_base.txt", "退货换货及会员政策", now, json.dumps({"type": "policy", "version": "1.0"}, ensure_ascii=False)))

        # 简单分块（按段落）
        paragraphs = [p.strip() for p in doc_content.strip().split("\n\n") if p.strip()]

        for i, para in enumerate(paragraphs):
            chunk_id = f"{doc_id}_chunk_{i}"
            cursor.execute("""
                INSERT OR IGNORE INTO chunks (chunk_id, doc_id, chunk_index, text, embedding)
                VALUES (?, ?, ?, ?, ?)
            """, (chunk_id, doc_id, i, para, None))  # embedding 为 None（关键词模式）

        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM documents")
        doc_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM chunks")
        chunk_count = cursor.fetchone()[0]

        logger.info(f"✓ 知识库文档: {doc_count} 篇，分块: {chunk_count} 个")

    except Exception as e:
        logger.error(f"插入知识库数据失败: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()


def main():
    """主函数"""
    try:
        logger.info("开始初始化数据库...")

        # 1. 初始化数据库（创建表）
        init_database("platform.db")
        logger.info("✓ 数据库表创建完成")

        # 2. 插入种子数据
        logger.info("\n开始插入种子数据...")
        seed_customers()
        seed_orders()
        seed_sales()
        seed_kg_triples()
        seed_knowledge_base()

        logger.info("\n✅ 种子数据初始化完成！")

    except Exception as e:
        logger.error(f"\n❌ 种子数据初始化失败: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
