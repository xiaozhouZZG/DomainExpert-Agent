"""工具注册表"""
from typing import List, Callable, Dict, Any

# 全局工具注册表
_TOOL_REGISTRY: Dict[str, Callable] = {}


def register_tool(name: str):
    """工具注册装饰器 - 必须在 @tool 之前使用"""
    def decorator(func: Callable):
        # 注册已经被 @tool 装饰后的函数
        _TOOL_REGISTRY[name] = func
        return func
    return decorator


def get_tools_for_agent(agent_name: str) -> List[Callable]:
    """
    获取指定 Agent 的工具实例

    参数:
        agent_name: Agent 名称

    返回:
        LangChain Tool 对象列表
    """
    from agents import get_agent_tools
    tool_names = get_agent_tools(agent_name)

    tools = []
    for tool_name in tool_names:
        if tool_name in _TOOL_REGISTRY:
            tools.append(_TOOL_REGISTRY[tool_name])

    return tools


def get_all_tools() -> Dict[str, Callable]:
    """获取所有已注册的工具"""
    return _TOOL_REGISTRY.copy()


def list_tools_for_agent(agent_name: str) -> List[Dict[str, str]]:
    """
    列出 Agent 可用工具的元数据

    返回:
        [{"name": "tool1", "description": "...", "args": {...}}, ...]
    """
    tools = get_tools_for_agent(agent_name)

    metadata = []
    for t in tools:
        metadata.append({
            "name": t.name,
            "description": t.description,
            "args_schema": t.args_schema.model_json_schema() if hasattr(t.args_schema, 'model_json_schema') else {}
        })

    return metadata
