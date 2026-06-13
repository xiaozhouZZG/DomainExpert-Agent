"""状态定义模块"""
from typing import TypedDict, List, Dict, Any, Annotated
import operator


class AgentState(TypedDict):
    """多智能体共享状态"""

    # 用户输入（累加器）
    messages: Annotated[List[Dict[str, Any]], operator.add]  # 消息历史(累加)

    # 编排信息
    current_agent: str  # 当前处理的 Agent
    next_action: str  # 下一步动作: route_to_xxx / finish / error
    iteration_count: int  # 迭代次数(防死循环)
    route_history: List[str]  # 路由历史（记录每次跳转的Agent）

    # 任务信息
    task_plan: str  # 任务分解计划
    completed_steps: List[str]  # 已完成步骤

    # 工具调用
    tool_calls: List[Dict[str, Any]]  # 工具调用记录
    tool_results: List[Dict[str, Any]]  # 工具返回结果

    # 知识检索
    retrieved_context: str  # RAG 检索到的上下文
    citations: List[str]  # 引用来源

    # 用户上下文
    current_customer_id: str  # 当前用户ID（用于工具按用户域过滤）
    collected_slots: Dict[str, Any]  # 跨Agent传递的槽位（订单号、商品ID等）

    # 元信息
    session_id: str
    trace_id: str  # 全链路追踪ID
    start_time: float
