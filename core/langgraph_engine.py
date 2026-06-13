"""LangGraph 编排引擎（需要 langgraph 依赖）"""
import os
import logging
from typing import List, Dict, Any, AsyncIterator
import time
import uuid

from .orchestrator import OrchestratorInterface, AgentMessage, ExecutionStep
from .state import AgentState
from .config_manager import ConfigManager

logger = logging.getLogger(__name__)


class LangGraphEngine(OrchestratorInterface):
    """LangGraph 真实编排引擎"""

    def __init__(self):
        self.llm = self._init_llm()
        self.graph = self._build_graph()
        logger.info("LangGraphEngine 初始化完成")

    def _init_llm(self):
        """初始化 LLM（优先从数据库读取配置）"""
        # 优先从数据库读取配置
        try:
            db_config = ConfigManager.get_llm_config()
            api_key = db_config.get("api_key") or os.getenv("LLM_API_KEY", "")
            base_url = db_config.get("base_url") or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
            model = db_config.get("model") or os.getenv("LLM_MODEL", "gpt-4o-mini")
            protocol = db_config.get("protocol", "openai")
            logger.info(f"[LangGraphEngine] 从数据库加载 LLM 配置: protocol={protocol}, model={model}, base_url={base_url}")
        except Exception as e:
            # 降级到环境变量
            logger.warning(f"[LangGraphEngine] 数据库配置读取失败，使用环境变量: {e}")
            api_key = os.getenv("LLM_API_KEY", "")
            base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
            model = os.getenv("LLM_MODEL", "gpt-4o-mini")
            protocol = "openai"

        # 根据协议选择客户端
        if protocol == "anthropic":
            from langchain_anthropic import ChatAnthropic
            logger.info(f"[LangGraphEngine] 使用 Anthropic 协议")
            return ChatAnthropic(
                api_key=api_key,
                base_url=base_url,
                model=model,
                temperature=0.7
            )
        else:
            # 默认使用 OpenAI 兼容协议
            from langchain_openai import ChatOpenAI
            logger.info(f"[LangGraphEngine] 使用 OpenAI 兼容协议")
            return ChatOpenAI(
                api_key=api_key,
                base_url=base_url,
                model=model,
                temperature=0.7
            )

    def _build_graph(self):
        """构建 LangGraph 状态图"""
        from langgraph.graph import StateGraph, END
        from langgraph.prebuilt import create_react_agent

        from agents import get_all_agents
        from tools.registry import get_tools_for_agent

        # 创建图
        workflow = StateGraph(AgentState)

        # Supervisor 节点
        workflow.add_node("supervisor", self._supervisor_node)

        # Agent 节点
        agents_config = get_all_agents()
        for agent_name, config in agents_config.items():
            tools = get_tools_for_agent(agent_name)
            system_message = config["system_prompt"]

            # 创建 react agent
            react_agent = create_react_agent(
                self.llm,
                tools,
                messages_modifier=system_message
            )

            # 包装以设置用户上下文
            def make_agent_node(agent_graph, name):
                def agent_node(state: AgentState):
                    import threading
                    # 设置线程本地变量供工具使用
                    threading.current_thread().current_customer_id = state.get("current_customer_id", "CUST001")
                    threading.current_thread().session_id = state.get("session_id", "UNKNOWN")
                    logger.info(f"[{name}] 设置用户上下文: {state.get('current_customer_id')}")
                    # 调用 agent graph
                    result = agent_graph.invoke(state)
                    # 记录 tool_calls 证据（检查所有消息）
                    if result.get("messages"):
                        for msg in result["messages"]:
                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                logger.info(f"[{name}] 🔧 LLM tool_calls: {msg.tool_calls}")
                    return result
                return agent_node

            workflow.add_node(agent_name, make_agent_node(react_agent, agent_name))

        # 设置入口
        workflow.set_entry_point("supervisor")

        # 条件路由
        workflow.add_conditional_edges(
            "supervisor",
            self._route_decision,
            {
                "customer_service_agent": "customer_service_agent",
                "order_agent": "order_agent",
                "analytics_agent": "analytics_agent",
                "report_agent": "report_agent",
                "finish": END
            }
        )

        # 专家回到 supervisor
        for agent in agents_config.keys():
            workflow.add_edge(agent, "supervisor")

        return workflow.compile()

    def _supervisor_node(self, state: AgentState) -> Dict[str, Any]:
        """Supervisor 决策（支持多跳路由，避免无意义重复）"""
        iteration = state.get("iteration_count", 0)
        route_history = state.get("route_history", [])

        # 最多4跳兜底
        if iteration >= 4:
            logger.info("[Supervisor] 达到最大迭代次数4跳，结束")
            return {"next_action": "finish"}

        # 获取用户查询和最后的 Agent 回复
        user_query = ""
        last_agent_reply = ""
        for msg in reversed(state["messages"]):
            # LangChain 消息对象
            if hasattr(msg, "type"):
                if msg.type == "human" and not user_query:
                    user_query = msg.content
                elif msg.type == "ai" and not last_agent_reply:
                    last_agent_reply = msg.content
            # 字典格式
            elif isinstance(msg, dict):
                if msg.get("role") == "user" and not user_query:
                    user_query = msg.get("content", "")
                elif msg.get("role") == "assistant" and not last_agent_reply:
                    last_agent_reply = msg.get("content", "")

            if user_query and last_agent_reply:
                break

        if not user_query:
            return {"next_action": "finish"}

        logger.info(f"[Supervisor] 用户查询: {user_query}, 路由历史: {route_history}")

        # 使用 LLM 评估任务完成度
        try:
            routing_prompt = f"""你是任务路由器。判断用户请求是否已完成，如未完成则选择下一个专家。

用户请求: {user_query}

已路由过的专家: {route_history}
最后回复: {last_agent_reply[:300] if last_agent_reply else '(无)'}

可用专家：
- customer_service_agent: 客服咨询、退换货、售后、知识库查询
- order_agent: 订单查询、修改、取消
- analytics_agent: 数据分析、库存查询、统计
- report_agent: 报表生成

判断规则：
1. 如果最后的回复已完整回答用户问题，回复 finish
2. 如果还缺某方面能力，选择对应专家（不能选已用过的，避免重复）
3. 最多3个不同专家，之后必须 finish

只回复一个专家名称或 finish。"""

            response = self.llm.invoke([{"role": "user", "content": routing_prompt}])
            decision = response.content.strip().lower()

            logger.info(f"[Supervisor] LLM 路由决策: {decision}")

            all_agents = ["customer_service_agent", "order_agent", "analytics_agent", "report_agent"]

            # 如果决策不是有效的 agent 名称，结束
            if decision not in all_agents:
                return {"next_action": "finish"}

            # 防止重复派已用过的 Agent
            if decision in route_history:
                logger.warning(f"[Supervisor] {decision} 已在历史中，避免重复，结束")
                return {"next_action": "finish"}

            # 记录路由历史
            new_route_history = route_history + [decision]

            return {
                "next_action": decision,
                "route_history": new_route_history,
                "iteration_count": iteration + 1
            }

        except Exception as e:
            logger.error(f"[Supervisor] LLM 路由失败: {e}")
            # 降级到简单规则
            keywords = {
                "customer_service_agent": ["退货", "换货", "咨询", "客服", "售后", "帮助", "问题"],
                "order_agent": ["订单", "查询订单", "取消订单", "修改订单"],
                "analytics_agent": ["分析", "数据", "统计", "销售额", "趋势"],
                "report_agent": ["报表", "生成报告", "导出", "excel", "pdf"]
            }

            for agent, words in keywords.items():
                if agent not in completed and any(w in user_query for w in words):
                    return {
                        "next_action": agent,
                        "iteration_count": state.get("iteration_count", 0) + 1
                    }

            # 默认路由到客服
            if "customer_service_agent" not in completed:
                return {
                    "next_action": "customer_service_agent",
                    "iteration_count": state.get("iteration_count", 0) + 1
                }

            return {"next_action": "finish"}

    def _route_decision(self, state: AgentState) -> str:
        """路由决策"""
        return state.get("next_action", "finish")

    async def execute(
        self,
        messages: List[AgentMessage],
        session_id: str,
        customer_id: str = "CUST001",
        config: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """同步执行"""
        logger.info(f"[LangGraphEngine] 开始执行 session_id={session_id}, customer_id={customer_id}")

        state = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "session_id": session_id,
            "trace_id": str(uuid.uuid4()),
            "start_time": time.time(),
            "current_customer_id": customer_id,
            "collected_slots": {},
            "iteration_count": 0,
            "completed_steps": [],
            "next_action": "",
            "current_agent": "",
            "task_plan": "",
            "tool_calls": [],
            "tool_results": [],
            "retrieved_context": "",
            "citations": []
        }

        try:
            logger.info(f"[LangGraphEngine] 调用 graph.invoke...")
            result = self.graph.invoke(state)
            logger.info(f"[LangGraphEngine] graph.invoke 完成，消息数={len(result.get('messages', []))}")

            # 提取最后一条 AI 回复
            final_response = None
            agent_name = "system"

            # LangGraph 返回的 messages 是 LangChain 消息对象列表
            for msg in reversed(result.get("messages", [])):
                # 处理 LangChain 消息对象
                if hasattr(msg, 'content'):
                    content = msg.content
                elif isinstance(msg, dict):
                    content = msg.get("content", "")
                else:
                    content = str(msg)

                # 检查是否是 assistant 消息
                if hasattr(msg, 'type') and msg.type == 'ai':
                    final_response = content
                    # 从 completed_steps 获取最后执行的 agent
                    completed = result.get("completed_steps", [])
                    if completed:
                        agent_name = completed[-1]
                    break
                elif isinstance(msg, dict) and msg.get("role") == "assistant":
                    final_response = content
                    completed = result.get("completed_steps", [])
                    if completed:
                        agent_name = completed[-1]
                    break

            # 如果没有找到 AI 回复，使用最后一条消息
            if not final_response and result.get("messages"):
                last_msg = result["messages"][-1]
                if hasattr(last_msg, 'content'):
                    final_response = last_msg.content
                elif isinstance(last_msg, dict):
                    final_response = last_msg.get("content", "")
                else:
                    final_response = str(last_msg)

            if not final_response:
                raise ValueError("LangGraph 未返回有效的 AI 回复")

            logger.info(f"[LangGraphEngine] 最终回复 agent={agent_name}, 内容长度={len(final_response)}")

            return {
                "final_response": final_response,
                "agent": agent_name,
                "trace_id": result["trace_id"],
                "elapsed_time": time.time() - result["start_time"],
                "steps": result.get("completed_steps", []),
                "tool_calls": result.get("tool_calls", []),
                "citations": result.get("citations", [])
            }

        except Exception as e:
            logger.error(f"[LangGraphEngine] 执行失败: {e}", exc_info=True)
            raise

    async def stream(
        self,
        messages: List[AgentMessage],
        session_id: str,
        config: Dict[str, Any]
    ) -> AsyncIterator[ExecutionStep]:
        """流式执行"""
        yield ExecutionStep(
            step_type="start",
            content="开始任务编排 (LangGraphEngine)",
            timestamp=time.time()
        )

        # LangGraph stream 实现
        state = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "session_id": session_id,
            "trace_id": str(uuid.uuid4()),
            "start_time": time.time(),
            "iteration_count": 0,
            "completed_steps": [],
            "next_action": "",
            "current_agent": "",
            "task_plan": "",
            "tool_calls": [],
            "tool_results": [],
            "retrieved_context": "",
            "citations": []
        }

        async for chunk in self.graph.astream(state):
            for node_name, node_output in chunk.items():
                yield ExecutionStep(
                    step_type="agent",
                    agent_name=node_name,
                    content=str(node_output),
                    timestamp=time.time()
                )

        yield ExecutionStep(
            step_type="final",
            content="任务完成",
            timestamp=time.time()
        )

    def get_state(self, session_id: str) -> Dict[str, Any]:
        """获取会话状态"""
        return {}
