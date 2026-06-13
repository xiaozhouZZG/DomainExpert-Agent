"""初始化测试数据：products 表 + orders 关联"""
import sqlite3
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def init_test_data():
    conn = sqlite3.connect('data/platform.db')
    cursor = conn.cursor()

    # 1. 创建 products 表
    print("[1/3] 创建 products 表...")
    cursor.execute("""
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
    """)

    # 2. 插入测试商品数据
    print("[2/3] 插入测试商品...")
    products = [
        ('SKU001', 'iPhone 15 Pro Max 256GB', '手机', 9999.00, 50, 'active', '苹果最新旗舰手机'),
        ('SKU002', 'MacBook Pro 14寸 M3', '电脑', 15999.00, 20, 'active', '专业级笔记本电脑'),
        ('SKU003', 'AirPods Pro 2代', '耳机', 1899.00, 3, 'active', '主动降噪耳机'),
        ('SKU004', 'iPad Air 128GB', '平板', 4799.00, 0, 'out_of_stock', '售罄补货中'),
        ('SKU005', '小米13 Ultra 16+512GB', '手机', 6999.00, 100, 'active', '影像旗舰'),
        ('SKU006', '华为 Mate 60 Pro', '手机', 6999.00, 2, 'active', '库存紧张'),
        ('SKU007', '戴尔XPS 15 笔记本', '电脑', 12999.00, 15, 'active', '高性能轻薄本'),
        ('SKU008', 'Sony WH-1000XM5 耳机', '耳机', 2599.00, 0, 'out_of_stock', '已售罄'),
    ]

    for sku, name, category, price, stock, status, desc in products:
        cursor.execute("""
            INSERT OR REPLACE INTO products (sku, name, category, price, stock, status, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (sku, name, category, price, stock, status, desc))

    print(f"✓ 插入 {len(products)} 个商品")

    # 3. 更新 orders 表，关联真实 SKU
    print("[3/3] 更新 orders 表...")
    cursor.execute("UPDATE orders SET product_id = 'SKU001' WHERE order_id = '202606010001'")
    cursor.execute("UPDATE orders SET product_id = 'SKU003' WHERE order_id = '202606010002'")
    cursor.execute("UPDATE orders SET product_id = 'SKU005' WHERE order_id = '202606010003'")

    conn.commit()
    conn.close()

    print("\n========== 数据初始化完成 ==========")
    print("商品数据:")
    print("  - SKU001: iPhone 15 Pro Max (库存充足: 50)")
    print("  - SKU003: AirPods Pro 2代 (库存紧张: 3)")
    print("  - SKU004: iPad Air (售罄: 0)")
    print("  - SKU006: 华为 Mate 60 Pro (库存紧张: 2)")
    print("  - SKU008: Sony 耳机 (售罄: 0)")
    print("\n订单关联:")
    print("  - 202606010001 → SKU001 (iPhone)")
    print("  - 202606010002 → SKU003 (AirPods)")
    print("  - 202606010003 → SKU005 (小米13)")

if __name__ == "__main__":
    init_test_data()
