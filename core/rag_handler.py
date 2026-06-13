"""RAG 检索处理器（支持命中/未命中决策和转人工）"""
import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime

from database.connection import get_db_connection
from knowledge.rag_engine import get_rag_engine

logger = logging.getLogger(__name__)


class RAGHandler:
    """RAG 检索处理器"""

    def __init__(self):
        self.rag_engine = get_rag_engine()

    def get_threshold(self) -> float:
        """从数据库读取 RAG 阈值"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_config WHERE key = ?", ("rag_threshold",))
            row = cursor.fetchone()
            conn.close()
            if row:
                return float(row[0])
        except Exception as e:
            logger.warning(f"读取 RAG 阈值失败，使用默认值: {e}")
        return 0.35

    def get_customer_service_channels(self) -> List[Dict[str, str]]:
        """获取客服渠道配置"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_config WHERE key = ?", ("customer_service_channels",))
            row = cursor.fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
        except Exception as e:
            logger.warning(f"读取客服渠道失败，使用默认值: {e}")

        return [
            {"name": "在线客服", "type": "chat", "value": "点击右下角在线客服"},
            {"name": "客服电话", "type": "phone", "value": "400-123-4567"}
        ]

    def search(self, query: str, top_k: int = 3) -> Dict[str, Any]:
        """
        执行 RAG 检索并返回结果

        Returns:
            {
                "hit": bool,  # 是否命中
                "score": float,  # 最高相关度分数
                "results": List[Dict],  # 检索结果
                "threshold": float  # 使用的阈值
            }
        """
        threshold = self.get_threshold()
        logger.info(f"[RAG] 检索: query={query[:50]}, threshold={threshold}")

        try:
            results = self.rag_engine.search(query, top_k=top_k, threshold=0)  # 不在引擎层过滤

            if not results:
                logger.info(f"[RAG] 未命中: 知识库为空或无结果")
                return {
                    "hit": False,
                    "score": 0.0,
                    "results": [],
                    "threshold": threshold
                }

            # 获取最高分数
            top_score = results[0].get("score", 0.0)
            hit = top_score >= threshold

            logger.info(f"[RAG] {'✓ 命中' if hit else '✗ 未命中'}: top_score={top_score:.3f}, threshold={threshold}")

            return {
                "hit": hit,
                "score": top_score,
                "results": results,
                "threshold": threshold
            }

        except Exception as e:
            logger.error(f"[RAG] 检索失败: {e}", exc_info=True)
            return {
                "hit": False,
                "score": 0.0,
                "results": [],
                "threshold": threshold,
                "error": str(e)
            }

    def create_handoff_ticket(
        self,
        session_id: str,
        user_id: str,
        question: str
    ) -> int:
        """创建转人工工单"""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO handoff_tickets (session_id, user_id, question, created_at, status)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, user_id, question, datetime.now().isoformat(), "待处理"))

            ticket_id = cursor.lastrowid
            conn.commit()

            logger.info(f"[RAG] 创建转人工工单: ticket_id={ticket_id}, session={session_id}")
            return ticket_id

        except Exception as e:
            logger.error(f"[RAG] 创建工单失败: {e}", exc_info=True)
            if conn:
                conn.rollback()
            return 0
        finally:
            if conn:
                conn.close()

    def generate_handoff_response(
        self,
        question: str,
        ticket_id: int
    ) -> str:
        """生成转人工客服的友好回复"""
        channels = self.get_customer_service_channels()

        channels_text = "\n".join([
            f"• {ch['name']}: {ch['value']}"
            for ch in channels
        ])

        response = f"""非常抱歉，关于"{question}"这个问题，我暂时没有查到相关资料。

我已为您转接人工客服，您也可以通过以下方式联系我们：

{channels_text}

工单编号：#{ticket_id}
我已记录您的问题，客服会尽快跟进处理。"""

        return response


# 全局单例
_rag_handler = None


def get_rag_handler() -> RAGHandler:
    """获取 RAG 处理器单例"""
    global _rag_handler
    if _rag_handler is None:
        _rag_handler = RAGHandler()
    return _rag_handler
