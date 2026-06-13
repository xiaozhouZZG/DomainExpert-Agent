"""编排器抽象接口"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, AsyncIterator
from dataclasses import dataclass


@dataclass
class AgentMessage:
    """智能体消息"""
    role: str  # user / assistant / system / tool
    content: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ExecutionStep:
    """执行步骤(用于流式推送)"""
    step_type: str  # start / plan / route / agent / tool / final / error
    agent_name: Optional[str] = None
    content: str = ""
    metadata: Optional[Dict[str, Any]] = None
    timestamp: float = 0.0


class OrchestratorInterface(ABC):
    """编排器统一接口"""

    @abstractmethod
    async def execute(
        self,
        messages: List[AgentMessage],
        session_id: str,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """同步执行(返回完整结果)"""
        pass

    @abstractmethod
    async def stream(
        self,
        messages: List[AgentMessage],
        session_id: str,
        config: Dict[str, Any]
    ) -> AsyncIterator[ExecutionStep]:
        """流式执行(逐步返回)"""
        pass

    @abstractmethod
    def get_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话状态"""
        pass
