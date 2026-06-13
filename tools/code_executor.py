"""代码执行沙箱工具"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import json
import sys
from io import StringIO
import traceback

from .registry import register_tool


class ExecuteCodeInput(BaseModel):
    """执行代码输入"""
    code: str = Field(..., description="Python代码")
    timeout: int = Field(10, description="超时时间(秒，当前未强制实现)")


@register_tool("execute_code")
@tool(args_schema=ExecuteCodeInput)
def execute_code(code: str, timeout: int = 10) -> str:
    """
    执行Python代码(受限沙箱环境)

    参数:
        code: Python代码
        timeout: 超时时间(秒，当前未强制实现，仅作参数占位)

    返回:
        执行结果或错误信息

    安全限制:
    - 仅允许基础内置函数(无 import/open/eval/exec 等危险操作)
    - 无文件系统访问、无网络访问
    - 当前实现基于 exec() + 受限 builtins，非真隔离
    - 生产环境必须使用容器隔离(Docker/gVisor)或专用沙箱(PyPy Sandbox)

    注意:
    - timeout 参数当前未实现真正的超时控制
    - 如需真超时，建议使用 multiprocessing.Pool.apply_async 或 signal.alarm
    """
    try:
        # 捕获标准输出
        old_stdout = sys.stdout
        sys.stdout = captured_output = StringIO()

        # 限制可用的内置函数(禁止 import/open 等)
        safe_globals = {
            '__builtins__': {
                'print': print,
                'range': range,
                'len': len,
                'sum': sum,
                'max': max,
                'min': min,
                'abs': abs,
                'round': round,
                'sorted': sorted,
                'enumerate': enumerate,
                'zip': zip,
                'map': map,
                'filter': filter,
                'list': list,
                'dict': dict,
                'set': set,
                'tuple': tuple,
                'str': str,
                'int': int,
                'float': float,
            }
        }

        # 执行代码
        exec(code, safe_globals, {})

        # 恢复标准输出
        sys.stdout = old_stdout
        output = captured_output.getvalue()

        result = {
            "status": "success",
            "output": output if output else "(无输出)",
            "code": code[:100] + "..." if len(code) > 100 else code
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        sys.stdout = old_stdout
        error_trace = traceback.format_exc()

        result = {
            "status": "error",
            "error": str(e),
            "traceback": error_trace
        }

        return json.dumps(result, ensure_ascii=False, indent=2)
