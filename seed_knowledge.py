"""写入知识库数据到 chunks 表"""
import sqlite3
import sys
import io
import uuid
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def seed_knowledge():
    conn = sqlite3.connect('data/platform.db')
    cursor = conn.cursor()

    # 检查 documents 表是否存在
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'")
    if not cursor.fetchone():
        print("[ERROR] documents 表不存在")
        return

    # 知识库内容
    knowledge_items = [
        {
            "title": "退货政策说明",
            "content": "退货政策：1) 签收后7天内可申请退货；2) 商品需未使用、包装完好；3) 不支持定制商品、生鲜食品、贴身用品退货；4) 退货流程：联系客服 → 填写退货单 → 寄回商品 → 3-5个工作日退款；5) 非质量问题退货运费由客户承担。",
            "category": "售后政策"
        },
        {
            "title": "换货政策说明",
            "content": "换货政策：1) 签收后15天内可申请换货；2) 仅限质量问题或发错货；3) 商品需完整包装；4) 换货流程：联系客服 → 寄回旧品 → 发出新品；5) 质量问题换货运费由平台承担。",
            "category": "售后政策"
        },
        {
            "title": "产品保修政策",
            "content": "保修政策：1) 电子产品提供1年质保；2) 保修期内免费维修或更换；3) 人为损坏不在保修范围；4) 保修需提供购买凭证；5) 保修期从购买日期起算。",
            "category": "售后政策"
        },
        {
            "title": "物流配送说明",
            "content": "物流配送：1) 订单支付后1-2个工作日发货；2) 支持顺丰、京东、德邦等物流；3) 部分地区支持次日达；4) 订单金额满299元包邮，否则运费10元；5) 可在订单详情页查看物流信息。",
            "category": "物流配送"
        },
        {
            "title": "会员等级权益",
            "content": "会员等级：1) 普通会员：无折扣；2) 银卡会员：累计消费满1000元，享95折；3) 金卡会员：累计消费满5000元，享9折+生日礼品；4) 钻石会员：累计消费满10000元，享85折+专属客服+优先发货。",
            "category": "会员体系"
        },
        {
            "title": "支付方式说明",
            "content": "支付方式：支持微信支付、支付宝、银行卡支付、花呗分期。大额订单可联系客服申请货到付款（需审核）。所有支付渠道均采用加密传输，保障资金安全。",
            "category": "支付相关"
        },
        {
            "title": "发票开具说明",
            "content": "发票开具：1) 支持电子发票和纸质发票；2) 下单时备注发票抬头和税号；3) 电子发票随货发送到邮箱；4) 纸质发票随货寄出；5) 发票内容可选商品明细或办公用品。",
            "category": "发票相关"
        },
        {
            "title": "常见问题FAQ",
            "content": "常见问题：1) 如何修改收货地址？答：支付前可自行修改，支付后联系客服；2) 如何取消订单？答：未发货订单可在订单页直接取消；3) 如何联系客服？答：工作日9:00-18:00在线客服，或拨打400-XXX-XXXX；4) 支持货到付款吗？答：部分地区支持，需联系客服确认。",
            "category": "常见问题"
        },
    ]

    print(f"[1/2] 插入 {len(knowledge_items)} 条知识库数据...")

    now = datetime.now().isoformat()

    for idx, item in enumerate(knowledge_items):
        doc_id = f"KB{str(uuid.uuid4())[:8]}"
        chunk_id = f"{doc_id}_chunk_0"

        # 插入 document
        cursor.execute("""
            INSERT OR IGNORE INTO documents (doc_id, file_path, title, created_at, metadata)
            VALUES (?, ?, ?, ?, ?)
        """, (doc_id, f"knowledge/{item['title']}.txt", item['title'], now, f'{{"category":"{item["category"]}"}}'))

        # 插入 chunk（不使用 created_at，因为添加失败）
        cursor.execute("""
            INSERT OR REPLACE INTO chunks (chunk_id, doc_id, chunk_index, text, category, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (chunk_id, doc_id, 0, item['content'], item['category'], item['title']))

    conn.commit()

    # 验证
    cursor.execute("SELECT COUNT(*) FROM chunks WHERE category IS NOT NULL")
    count = cursor.fetchone()[0]

    conn.close()

    print(f"✓ 知识库数据写入完成")
    print(f"\n[2/2] 验证数据...")
    print(f"  chunks 表中有 {count} 条分类数据")

    print("\n========== 知识库内容 ==========")
    for item in knowledge_items:
        print(f"  - [{item['category']}] {item['title']}")

if __name__ == "__main__":
    seed_knowledge()
