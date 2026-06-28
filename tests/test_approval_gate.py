"""②审批闭环 子项1 行为测试: is_approval_approved 三重校验 + set_approval_status expect_current。

覆盖:
- 命门1: pending→approve→approved 闭环, 批准前 pending 不放行
- 命门3: 跨动作(list↔ship) / 跨对象(订单A↔B) 挪用拦截
- 命门4: 防重复批准 / 防复活已拒单

全程临时 sqlite, 不碰真库、不起浏览器。
"""
import pytest

from database import connection
from core import xianyu_service


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "platform.db"
    monkeypatch.setattr(connection, "DB_PATH", str(db))
    connection.ensure_tables()
    return str(db)


def _insert(approval_id, workflow_type, status="pending", order_id=None):
    conn = connection.get_db_connection()
    try:
        conn.execute(
            "INSERT INTO approvals (approval_id, workflow_type, status, order_id, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (approval_id, workflow_type, status, order_id),
        )
        conn.commit()
    finally:
        conn.close()


# ===== 命门1: 批准闭环 =====

def test_approve_pending_to_approved(tmp_db):
    _insert("a1", "xianyu_list_item", "pending")
    assert xianyu_service.set_approval_status("a1", "approved", expect_current="pending") is True
    assert xianyu_service.is_approval_approved("a1", "xianyu_list_item") is True


def test_pending_not_approved_before_approve(tmp_db):
    _insert("a2", "xianyu_list_item", "pending")
    # 批准前 pending → 执行校验应 False (被拒, 不焊死也不放水)
    assert xianyu_service.is_approval_approved("a2", "xianyu_list_item") is False


# ===== 命门3: 跨动作 / 跨对象挪用拦截 =====

def test_cross_action_misuse_blocked(tmp_db):
    """approved 的 list 单, 拿去当 ship 校验 → False。"""
    _insert("a3", "xianyu_list_item", "pending")
    xianyu_service.set_approval_status("a3", "approved", expect_current="pending")
    assert xianyu_service.is_approval_approved("a3", "xianyu_list_item") is True    # 对的动作放行
    assert xianyu_service.is_approval_approved("a3", "xianyu_ship_order") is False  # 跨动作拦


def test_cross_object_misuse_blocked(tmp_db):
    """approved 订单A ship 单 → 发订单B → False。"""
    _insert("a4", "xianyu_ship_order", "pending", order_id="ORDER_A")
    xianyu_service.set_approval_status("a4", "approved", expect_current="pending")
    assert xianyu_service.is_approval_approved("a4", "xianyu_ship_order", order_id="ORDER_A") is True   # 对的订单
    assert xianyu_service.is_approval_approved("a4", "xianyu_ship_order", order_id="ORDER_B") is False  # 跨对象拦
    # 不带 order_id 校验(None) 时只看 status+wf_type, 仍 True (order_id is None 跳过订单校验)
    assert xianyu_service.is_approval_approved("a4", "xianyu_ship_order") is True


# ===== 命门4: 防重复批准 / 防复活已拒单 =====

def test_no_double_approve(tmp_db):
    _insert("a5", "xianyu_list_item", "pending")
    assert xianyu_service.set_approval_status("a5", "approved", expect_current="pending") is True
    # 已 approved 再批 (expect pending) → rowcount 0
    assert xianyu_service.set_approval_status("a5", "approved", expect_current="pending") is False


def test_no_revive_rejected(tmp_db):
    _insert("a6", "xianyu_list_item", "rejected")
    # rejected 想复活成 approved (expect pending) → rowcount 0
    assert xianyu_service.set_approval_status("a6", "approved", expect_current="pending") is False
    assert xianyu_service.is_approval_approved("a6", "xianyu_list_item") is False


def test_nonexistent_approval(tmp_db):
    assert xianyu_service.is_approval_approved("nope", "xianyu_list_item") is False
    assert xianyu_service.set_approval_status("nope", "approved", expect_current="pending") is False
