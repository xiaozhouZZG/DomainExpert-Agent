"""报表生成工具"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import json
from typing import Optional

from .registry import register_tool


class GenerateReportInput(BaseModel):
    """生成报表输入"""
    report_type: str = Field(..., description="报表类型: sales_daily/sales_monthly/customer_analysis")
    format: str = Field("pdf", description="输出格式: pdf/excel/html")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")


@register_tool("generate_report")
@tool(args_schema=GenerateReportInput)
def generate_report(
    report_type: str,
    format: str = "pdf",
    start_date: str = "",
    end_date: str = ""
) -> str:
    """
    生成业务报表

    参数:
        report_type: 报表类型(sales_daily/sales_monthly/customer_analysis)
        format: 输出格式(pdf/excel/html)
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD

    返回:
        报表文件路径或下载链接
    """
    try:
        # TODO: 实际集成报表生成库(ReportLab/openpyxl/jinja2)

        # 模拟生成
        filename = f"{report_type}_{start_date}_to_{end_date}.{format}"

        result = {
            "status": "success",
            "message": "报表已生成",
            "filename": filename,
            "download_url": f"/api/reports/download/{filename}",
            "report_type": report_type,
            "format": format,
            "date_range": f"{start_date} 至 {end_date}"
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)
