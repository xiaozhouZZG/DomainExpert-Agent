"""数据库查询工具"""
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import json
from datetime import datetime

from .registry import register_tool
from database.connection import get_db_connection


class QueryCustomerInput(BaseModel):
    """查询客户输入"""
    customer_id: Optional[str] = Field(None, description="客户ID")
    phone: Optional[str] = Field(None, description="手机号")
    email: Optional[str] = Field(None, description="邮箱")


@register_tool("query_customer")
@tool(args_schema=QueryCustomerInput)
def query_customer(
    customer_id: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None
) -> str:
    """
    查询客户信息（仅返回当前用户，保护隐私）

    何时调用：需要了解客户基本信息、等级、历史订单时

    参数:
        customer_id: 客户ID（系统自动填充，外部调用无需传）
        phone: 手机号（内部使用）
        email: 邮箱（内部使用）

    返回:
        JSON格式的当前用户信息
    """
    import logging
    import threading
    logger = logging.getLogger(__name__)

    # 强制使用当前用户
    current_user = getattr(threading.current_thread(), 'current_customer_id', 'CUST001')
    logger.info(f"【TOOL】query_customer - current_user={current_user}")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM customers WHERE customer_id = ?", (current_user,))
        row = cursor.fetchone()

        if not row:
            return json.dumps({"error": "未找到客户信息"}, ensure_ascii=False)

        columns = [desc[0] for desc in cursor.description]
        result = dict(zip(columns, row))

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"查询客户失败: {e}")
        return json.dumps({"error": f"查询失败: {str(e)}"}, ensure_ascii=False)
    finally:
        if conn:
            conn.close()


class QueryOrderInput(BaseModel):
    """查询订单输入"""
    order_id: Optional[str] = Field(None, description="订单ID")
    customer_id: Optional[str] = Field(None, description="客户ID")
    status: Optional[str] = Field(None, description="订单状态")
    limit: int = Field(10, description="返回记录数")


@register_tool("query_order")
@tool(args_schema=QueryOrderInput)
def query_order(
    order_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10
) -> str:
    """
    查询订单信息（按用户域过滤，保护数据安全）

    参数:
        order_id: 订单ID（可选，指定时会校验归属）
        customer_id: 客户ID（系统自动填充当前用户，外部调用无需传）
        status: 订单状态(pending/paid/shipped/completed/cancelled)
        limit: 返回记录数(默认10)

    返回:
        JSON格式的订单列表（带商品详情）
    """
    import logging
    logger = logging.getLogger(__name__)

    # 获取当前用户上下文（从线程本地变量或默认CUST001）
    import threading
    current_user = getattr(threading.current_thread(), 'current_customer_id', 'CUST001')

    logger.info(f"【TOOL】query_order - current_user={current_user}, order_id={order_id}, status={status}")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 如果指定了订单号，先校验归属
        if order_id:
            cursor.execute("SELECT customer_id FROM orders WHERE order_id = ?", (order_id,))
            row = cursor.fetchone()
            if not row:
                return json.dumps({"error": "订单不存在"}, ensure_ascii=False)
            if row[0] != current_user:
                logger.warning(f"【越权尝试】用户 {current_user} 尝试访问订单 {order_id}（归属 {row[0]}）")
                return json.dumps({"error": "无权访问该订单"}, ensure_ascii=False)

        # 查询订单（JOIN products）
        query = """
            SELECT
                o.order_id, o.customer_id, o.product_id, o.amount, o.status, o.created_at,
                p.name as product_name,
                p.category as product_category,
                p.stock as product_stock,
                p.price as product_price
            FROM orders o
            LEFT JOIN products p ON o.product_id = p.sku
            WHERE o.customer_id = ?
        """
        params = [current_user]

        if order_id:
            query += " AND o.order_id = ?"
            params.append(order_id)
        if status:
            query += " AND o.status = ?"
            params.append(status)

        query += f" ORDER BY o.created_at DESC LIMIT {min(limit, 100)}"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            return json.dumps({"error": "未找到订单"}, ensure_ascii=False)

        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in rows]

        return json.dumps({
            "total": len(results),
            "orders": results,
            "customer_id": current_user
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"查询订单失败: {e}", exc_info=True)
        return json.dumps({"error": f"查询失败: {str(e)}"}, ensure_ascii=False)
    finally:
        if conn:
            conn.close()


class QuerySalesInput(BaseModel):
    """查询销售数据输入"""
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")
    product_id: Optional[str] = Field(None, description="产品ID")
    group_by: Optional[str] = Field(None, description="聚合维度: date/product/customer")


@register_tool("query_sales")
@tool(args_schema=QuerySalesInput)
def query_sales(
    start_date: str,
    end_date: str,
    product_id: Optional[str] = None,
    group_by: Optional[str] = None
) -> str:
    """
    查询销售数据(支持聚合)

    参数:
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        product_id: 产品ID(可选)
        group_by: 聚合维度: date/product/customer(可选)

    返回:
        JSON格式的销售数据
    """
    conn = None
    try:
        # 日期校验
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")

        conn = get_db_connection()
        cursor = conn.cursor()

        if group_by:
            # 聚合查询
            group_field = {
                "date": "DATE(sale_date)",
                "product": "product_id",
                "customer": "customer_id"
            }.get(group_by, "DATE(sale_date)")

            query = f"""
                SELECT
                    {group_field} as dimension,
                    COUNT(*) as count,
                    SUM(amount) as total_amount,
                    AVG(amount) as avg_amount
                FROM sales
                WHERE sale_date BETWEEN ? AND ?
            """
            params = [start_date, end_date]

            if product_id:
                query += " AND product_id = ?"
                params.append(product_id)

            query += f" GROUP BY {group_field} ORDER BY dimension"
        else:
            # 明细查询
            query = "SELECT * FROM sales WHERE sale_date BETWEEN ? AND ?"
            params = [start_date, end_date]

            if product_id:
                query += " AND product_id = ?"
                params.append(product_id)

            query += " ORDER BY sale_date DESC LIMIT 100"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            return json.dumps({"error": "未找到销售数据"}, ensure_ascii=False)

        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in rows]

        return json.dumps({"total": len(results), "data": results}, ensure_ascii=False, indent=2)

    except ValueError as e:
        return json.dumps({"error": f"日期格式错误: {str(e)}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"查询失败: {str(e)}"}, ensure_ascii=False)
    finally:
        if conn:
            conn.close()


class QueryInventoryInput(BaseModel):
    """查询库存输入"""
    product_id: Optional[str] = Field(None, description="商品ID/SKU")
    category: Optional[str] = Field(None, description="商品分类")


@register_tool("query_inventory")
@tool(args_schema=QueryInventoryInput)
def query_inventory(
    product_id: Optional[str] = None,
    category: Optional[str] = None
) -> str:
    """
    查询商品库存信息

    参数:
        product_id: 商品ID/SKU（优先使用）
        category: 商品分类

    返回:
        JSON格式的库存信息
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"【TOOL】query_inventory - product_id={product_id}, category={category}")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        query = "SELECT sku, name, category, stock, price FROM products WHERE 1=1"
        params = []

        if product_id:
            query += " AND sku = ?"
            params.append(product_id)
        if category:
            query += " AND category = ?"
            params.append(category)

        query += " LIMIT 50"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            return json.dumps({"error": "未找到商品"}, ensure_ascii=False)

        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in rows]

        return json.dumps({"total": len(results), "products": results}, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"查询库存失败: {e}")
        return json.dumps({"error": f"查询失败: {str(e)}"}, ensure_ascii=False)
    finally:
        if conn:
            conn.close()
