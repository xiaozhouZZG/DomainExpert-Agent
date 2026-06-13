"""记忆管理工具"""
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


def apply_sliding_window(
    messages: List[Dict[str, Any]],
    window_size: int = 10
) -> List[Dict[str, Any]]:
    """
    滑动窗口：保留最近 N 轮对话

    Args:
        messages: 消息列表
        window_size: 窗口大小（轮数），默认10轮=20条消息

    Returns:
        截断后的消息列表
    """
    # 1 轮 = 1 user + 1 assistant = 2 条消息
    max_messages = window_size * 2

    if len(messages) <= max_messages:
        return messages

    # 保留系统消息（如果有）+ 最近 N 轮
    system_messages = [m for m in messages if m.get("role") == "system"]
    user_assistant_messages = [m for m in messages if m.get("role") in ["user", "assistant"]]

    recent_messages = user_assistant_messages[-max_messages:]

    logger.info(f"应用滑动窗口: {len(messages)} -> {len(system_messages) + len(recent_messages)}")
    return system_messages + recent_messages


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """
    估算消息列表的 Token 数

    粗略估算：1 Token ≈ 0.75 汉字 ≈ 4 英文字符
    """
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return int(total_chars * 1.5)  # 保守估计


async def summarize_history(
    llm_client,
    messages: List[Dict[str, Any]]
) -> str:
    """
    用 LLM 压缩历史对话为摘要

    Args:
        llm_client: LLM 客户端
        messages: 消息列表

    Returns:
        摘要文本（约100字）
    """
    # 构建需要摘要的对话
    history_text = "\n".join([
        f"{'用户' if m['role']=='user' else '客服'}: {m['content']}"
        for m in messages
    ])

    prompt = f"""请将以下客服对话压缩为简短摘要（100字以内），保留关键信息：

{history_text}

摘要："""

    try:
        response = await llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3
        )
        return response.get("content", "")
    except Exception as e:
        logger.error(f"生成摘要失败: {e}")
        return ""
