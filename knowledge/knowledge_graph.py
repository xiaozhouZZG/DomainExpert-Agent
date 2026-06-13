"""知识图谱（三元组存储）"""
from typing import List, Dict, Any
import logging
from datetime import datetime

from database.connection import get_db_connection

logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """简单知识图谱（三元组存储）"""

    def add_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float = 1.0,
        source: str = None
    ) -> bool:
        """添加三元组"""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO kg_triples (subject, predicate, object, confidence, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (subject, predicate, obj, confidence, source, datetime.now().isoformat()))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"添加三元组失败: {str(e)}")
            return False
        finally:
            if conn:
                conn.close()

    def query_by_subject(self, subject: str) -> List[Dict[str, Any]]:
        """查询以指定实体为主语的所有三元组"""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT subject, predicate, object, confidence, source
                FROM kg_triples
                WHERE subject = ?
                ORDER BY confidence DESC
            """, (subject,))

            rows = cursor.fetchall()

            return [
                {
                    "subject": r[0],
                    "predicate": r[1],
                    "object": r[2],
                    "confidence": r[3],
                    "source": r[4]
                }
                for r in rows
            ]

        except Exception as e:
            logger.error(f"查询失败: {str(e)}")
            return []
        finally:
            if conn:
                conn.close()

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM kg_triples")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT subject) FROM kg_triples")
            subjects = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT predicate) FROM kg_triples")
            predicates = cursor.fetchone()[0]

            return {
                "total_triples": total,
                "unique_subjects": subjects,
                "unique_predicates": predicates
            }

        except Exception as e:
            return {"error": str(e)}
        finally:
            if conn:
                conn.close()
