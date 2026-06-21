"""工具模块初始化"""
from .registry import register_tool, get_tools_for_agent, get_all_tools, list_tools_for_agent, _TOOL_REGISTRY

# 导入所有工具以触发注册
from . import database
from . import notification
from . import knowledge_search
from . import approval
from . import code_executor
from . import report_gen
from . import handoff
from . import xianyu

__all__ = [
    "register_tool",
    "get_tools_for_agent",
    "get_all_tools",
    "list_tools_for_agent",
    "_TOOL_REGISTRY",
]
