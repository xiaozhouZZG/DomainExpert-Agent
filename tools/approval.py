"""审批流工具"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import json
import uuid
from datetime import datetime

from .registry import register_tool
from database.connection import get_db_connection


class ApproveWorkflowInput(BaseModel):
    """发起审批输入"""
    workflow_type: str = Field(..., description="审批类型: refund/exchange/expense")
    title: str = Field(..., description="审批标题")
    content: str = Field(..., description="审批内容")
    amount: float = Field(0.0, description="涉及金额")


@register_tool("approve_workflow")
@tool(args_schema=ApproveWorkflowInput)
def approve_workflow(
    workflow_type: str,
    title: str,
    content: str,
    amount: float = 0.0
) -> str:
    """
    发起审批流程

    参数:
        workflow_type: 审批类型(refund/exchange/expense)
        title: 审批标题
        content: 审批内容
        amount: 涉及金额(可选)

    返回:
        审批单ID和状态
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        approval_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        cursor.execute("""
            INSERT INTO approvals
            (approval_id, workflow_type, title, content, amount, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (approval_id, workflow_type, title, content, amount, "pending", now))

        conn.commit()

        result = {
            "status": "success",
            "approval_id": approval_id,
            "message": f"审批单已创建，审批类型: {workflow_type}",
            "next_step": "等待审批人处理"
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)
    finally:
        if conn:
            conn.close()
