"""通知工具（邮件/短信）"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import json
import logging
from typing import Optional

from .registry import register_tool

logger = logging.getLogger(__name__)


class SendEmailInput(BaseModel):
    """发送邮件输入"""
    to: str = Field(..., description="收件人邮箱")
    subject: str = Field(..., description="邮件主题")
    body: str = Field(..., description="邮件正文")
    cc: Optional[str] = Field(None, description="抄送人")


@register_tool("send_email")
@tool(args_schema=SendEmailInput)
def send_email(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None
) -> str:
    """
    发送邮件

    参数:
        to: 收件人邮箱
        subject: 邮件主题
        body: 邮件正文(支持HTML)
        cc: 抄送人(可选)

    返回:
        发送结果
    """
    try:
        # TODO: 实际集成邮件服务(SMTP/SendGrid/阿里云邮件)
        logger.info(f"发送邮件: {to} | {subject}")

        # 模拟发送
        result = {
            "status": "success",
            "message": "邮件已发送",
            "to": to,
            "subject": subject,
            "timestamp": "2026-06-03T12:00:00Z"
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)


class SendSmsInput(BaseModel):
    """发送短信输入"""
    phone: str = Field(..., description="手机号")
    content: str = Field(..., description="短信内容")


@register_tool("send_sms")
@tool(args_schema=SendSmsInput)
def send_sms(phone: str, content: str) -> str:
    """
    发送短信

    参数:
        phone: 手机号
        content: 短信内容(不超过500字)

    返回:
        发送结果
    """
    try:
        if len(content) > 500:
            return json.dumps({"status": "failed", "error": "短信内容超长"}, ensure_ascii=False)

        # TODO: 实际集成短信服务(阿里云/腾讯云)
        logger.info(f"发送短信: {phone} | {content[:20]}...")

        result = {
            "status": "success",
            "message": "短信已发送",
            "phone": phone,
            "timestamp": "2026-06-03T12:00:00Z"
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)
