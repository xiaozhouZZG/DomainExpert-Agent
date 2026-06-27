"""
统一检索出口 - 三段式置信度护栏

所有检索请求必须通过此出口，自动判定置信度并返回相应状态。
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from database.connection import get_db_connection
from knowledge.hybrid_rag_engine import get_hybrid_engine

logger = logging.getLogger(__name__)

# 默认阈值（基于 eval/NEGATIVE_EVAL_REPORT.md 的实验结果）
# 注意：这些是基于单商品小库（7个chunks）的临时值
# 当知识库扩充或数据变化时，必须用 eval/run_retrieval_eval.py 重新评估
DEFAULT_HIGH_THRESHOLD = 0.60
DEFAULT_LOW_THRESHOLD = 0.53
DEFAULT_GRAY_ACTION = "handoff"


def get_retrieval_thresholds() -> dict[str, Any]:
    """
    从数据库读取检索阈值配置

    Returns:
        {
            'high_threshold': float,  # >= 此值：高置信度，可直接作答
            'low_threshold': float,   # < 此值：无答案
            'gray_action': str        # 'handoff' 或 'fallback'
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT key, value FROM system_config WHERE key LIKE 'retrieval_%'"
        )
        config = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        high_threshold = float(config.get('retrieval_high_threshold', DEFAULT_HIGH_THRESHOLD))
        low_threshold = float(config.get('retrieval_low_threshold', DEFAULT_LOW_THRESHOLD))
        gray_action = config.get('retrieval_gray_action', DEFAULT_GRAY_ACTION)

        return {
            'high_threshold': high_threshold,
            'low_threshold': low_threshold,
            'gray_action': gray_action
        }
    except Exception as e:
        logger.warning(f"读取检索阈值配置失败，使用默认值: {e}")
        return {
            'high_threshold': DEFAULT_HIGH_THRESHOLD,
            'low_threshold': DEFAULT_LOW_THRESHOLD,
            'gray_action': DEFAULT_GRAY_ACTION
        }


def search_with_confidence(
    query: str,
    top_k: int = 5,
    business_line: str | None = None,
    **search_kwargs
) -> dict[str, Any]:
    """
    统一检索出口 - 带置信度判定的检索

    三段式护栏：
    - high (>= 0.60): 高置信度，可直接作答
    - gray (0.53~0.60): 灰区，转人工或兜底话术
    - not_found (< 0.53): 无可靠答案

    Args:
        query: 查询问题
        top_k: 返回结果数量
        business_line: 业务线过滤
        **search_kwargs: 传递给 hybrid_search 的其他参数

    Returns:
        {
            'status': 'high' | 'gray' | 'not_found',
            'confidence_score': float,
            'results': list[dict],  # 检索结果（仅当 status != 'not_found'）
            'action': str,  # 推荐的后续动作
            'threshold_config': dict  # 当前使用的阈值配置（调试用）
        }
    """
    # 读取阈值配置
    thresholds = get_retrieval_thresholds()
    high_threshold = thresholds['high_threshold']
    low_threshold = thresholds['low_threshold']
    gray_action = thresholds['gray_action']

    # 执行检索
    engine = get_hybrid_engine()
    results = engine.search(query, top_k=top_k, **search_kwargs)

    # 如果指定了业务线，过滤结果
    if business_line:
        results = [r for r in results if r.get('business_line') == business_line]

    # 第2层: 读引擎索引健康（无论 results 空否都拿得到故障信号）
    health = engine.index_health() if hasattr(engine, "index_health") else {"status": "ok", "degraded_reason": None}
    index_status = health.get("status")

    # 降级信号合并，优先级: index 故障 > reranker 降级 > schema 漂移
    item_degraded = bool(results and results[0].get('_degraded', False))
    if index_status == "build_failed":
        degraded, degraded_reason = True, (health.get("degraded_reason") or "index_build_failed")
    elif item_degraded:
        degraded, degraded_reason = True, results[0].get('_degraded_reason')
    elif index_status == "schema_degraded":
        degraded, degraded_reason = True, (health.get("degraded_reason") or "schema_missing_columns")
    else:
        degraded, degraded_reason = False, None

    # build_failed: 系统故障，强制 not_found 转人工 + 响亮信号，绝不伪装成"正常无答案"
    if index_status == "build_failed":
        return {
            'status': 'not_found',
            'confidence_score': 0.0,
            'results': [],
            'action': 'handoff',
            'message': '检索系统索引异常，已转人工（系统故障，非正常无答案）',
            'threshold_config': thresholds,
            'degraded': True,
            'degraded_reason': degraded_reason,
        }

    # 判定置信度
    if not results:
        return {
            'status': 'not_found',
            'confidence_score': 0.0,
            'results': [],
            'action': 'fallback',
            'message': '知识库暂时没有相关信息',
            'threshold_config': thresholds,
            'degraded': degraded,
            'degraded_reason': degraded_reason,
        }

    top1_score = results[0].get('score', 0.0)

    # 三段式判定
    if top1_score >= high_threshold:
        # 高置信度：可直接作答
        return {
            'status': 'high',
            'confidence_score': top1_score,
            'results': results,
            'action': 'answer',
            'message': None,
            'threshold_config': thresholds,
            'degraded': degraded,
            'degraded_reason': degraded_reason,
        }

    elif top1_score >= low_threshold:
        # 灰区：不直接作答，转人工或兜底
        return {
            'status': 'gray',
            'confidence_score': top1_score,
            'results': results,
            'action': gray_action,  # 'handoff' 或 'fallback'
            'message': '这个问题我不太确定，建议转人工处理' if gray_action == 'handoff' else None,
            'threshold_config': thresholds,
            'degraded': degraded,
            'degraded_reason': degraded_reason,
        }

    else:
        # 低置信度：无可靠答案
        return {
            'status': 'not_found',
            'confidence_score': top1_score,
            'results': [],
            'action': 'handoff',
            'message': '抱歉，这个问题我没有找到可靠答案，帮您转人工处理',
            'threshold_config': thresholds,
            'degraded': degraded,
            'degraded_reason': degraded_reason,
        }


def format_retrieval_response(
    retrieval_result: dict[str, Any],
    context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    格式化检索结果为客服响应

    根据 status 决定后续动作：
    - high: 可作答（但硬事实仍需人审）
    - gray: 转人工/兜底话术
    - not_found: 转人工

    Args:
        retrieval_result: search_with_confidence 的返回值
        context: 额外上下文（如 conversation_id, buyer_message 等）

    Returns:
        {
            'should_answer': bool,  # 是否可以让 LLM 作答
            'should_handoff': bool,  # 是否转人工
            'fallback_message': str | None,  # 兜底话术
            'chunks': list[dict],  # 检索到的 chunks（给 LLM 用）
            'metadata': dict  # 元数据（置信度、阈值等）
        }
    """
    status = retrieval_result['status']
    action = retrieval_result['action']

    if status == 'high':
        # 高置信度：可作答
        return {
            'should_answer': True,
            'should_handoff': False,
            'fallback_message': None,
            'chunks': retrieval_result['results'],
            'metadata': {
                'confidence': 'high',
                'score': retrieval_result['confidence_score'],
                'threshold_config': retrieval_result['threshold_config']
            }
        }

    elif status == 'gray':
        # 灰区：默认转人工（卖东西时，宁可转人工不可乱答）
        if action == 'handoff':
            return {
                'should_answer': False,
                'should_handoff': True,
                'fallback_message': retrieval_result.get('message'),
                'chunks': [],
                'metadata': {
                    'confidence': 'gray',
                    'score': retrieval_result['confidence_score'],
                    'reason': '置信度不足，转人工处理',
                    'threshold_config': retrieval_result['threshold_config']
                }
            }
        else:
            # fallback: 使用兜底话术
            return {
                'should_answer': False,
                'should_handoff': False,
                'fallback_message': '这个问题我不太确定，建议您咨询客服获取准确信息',
                'chunks': [],
                'metadata': {
                    'confidence': 'gray',
                    'score': retrieval_result['confidence_score'],
                    'reason': '置信度不足，使用兜底话术',
                    'threshold_config': retrieval_result['threshold_config']
                }
            }

    else:  # not_found
        # 无答案：转人工
        return {
            'should_answer': False,
            'should_handoff': True,
            'fallback_message': retrieval_result.get('message'),
            'chunks': [],
            'metadata': {
                'confidence': 'not_found',
                'score': retrieval_result['confidence_score'],
                'reason': '知识库未找到相关信息',
                'threshold_config': retrieval_result['threshold_config']
            }
        }
