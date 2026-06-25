"""
异步文档处理任务队列

使用Python threading实现简单的后台任务队列
"""
import threading
import queue
import logging
import uuid
from datetime import datetime
from typing import Dict, Any
from dataclasses import dataclass

from knowledge.document_processor import DocumentProcessor
from database.connection import get_db_connection

logger = logging.getLogger(__name__)


@dataclass
class ProcessingTask:
    """处理任务"""
    doc_id: str
    file_path: str
    filename: str
    file_type: str
    business_line: str = None
    category: str = None
    product_name: str = None


class DocumentProcessingQueue:
    """文档处理队列（单例）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.task_queue = queue.Queue()
            self.processor = DocumentProcessor()
            self.worker_thread = None
            self.running = False
            self.initialized = True

    def start(self):
        """启动后台处理线程"""
        if self.running:
            return

        self.running = True
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        logger.info("✓ 文档处理队列已启动")

    def stop(self):
        """停止后台处理线程"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        logger.info("✓ 文档处理队列已停止")

    def submit_task(
        self,
        file_path: str,
        filename: str,
        file_type: str,
        business_line: str = None,
        category: str = None,
        product_name: str = None,
    ) -> str:
        """
        提交处理任务

        Args:
            file_path: 文件路径
            filename: 文件名
            file_type: 文件类型
            business_line: 业务线（上传表单，可选）
            category: 分类（上传表单，可选）
            product_name: 商品名（上传表单，可选）

        Returns:
            doc_id
        """
        doc_id = str(uuid.uuid4())

        # 创建处理状态记录
        self._create_processing_status(doc_id, filename)

        # 提交任务
        task = ProcessingTask(
            doc_id, file_path, filename, file_type,
            business_line=business_line,
            category=category,
            product_name=product_name,
        )
        self.task_queue.put(task)

        logger.info(f"任务已提交: {filename} (doc_id={doc_id})")
        return doc_id

    def _worker(self):
        """后台工作线程"""
        logger.info("后台处理线程已启动")

        while self.running:
            try:
                # 阻塞获取任务（timeout=1秒）
                task = self.task_queue.get(timeout=1)

                logger.info(f"开始处理任务: {task.filename}")

                try:
                    # 处理文档
                    result = self.processor.process_document(
                        task.file_path,
                        task.doc_id,
                        task.filename,
                        task.file_type,
                        business_line=task.business_line,
                        category=task.category,
                        product_name=task.product_name,
                    )
                    logger.info(f"✓ 任务完成: {task.filename} - {result['chunks']} 个块")

                    # P0-4: 文档成功入库后刷新索引，使新资料无需重启即可检索。
                    # 入库成功 ≠ 可检索成功：刷新失败时只标记 index_status，
                    # 不把已入库文档当成完全失败（document_status 仍为 completed）。
                    self._refresh_index_after_ingest(task.doc_id, task.filename)

                except Exception:
                    logger.exception(f"✗ 任务失败: {task.filename}")

                finally:
                    self.task_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logger.exception("工作线程异常")

        logger.info("后台处理线程已退出")

    def _create_processing_status(self, doc_id: str, filename: str):
        """创建处理状态记录（先创建documents记录以满足外键约束）"""
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # 先创建documents记录（满足外键约束）
            cursor.execute("""
                INSERT OR IGNORE INTO documents (doc_id, file_path, title, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
            """, (doc_id, filename, filename, datetime.now().isoformat(), '{}'))

            # 再创建处理状态记录
            cursor.execute("""
                INSERT INTO document_processing_status (
                    doc_id, filename, status, progress, total_blocks, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (doc_id, filename, "queued", 0, 0, datetime.now().isoformat()))

            conn.commit()
        except Exception as e:
            logger.exception("创建状态记录失败")
            conn.rollback()
        finally:
            conn.close()

    def _refresh_index_after_ingest(self, doc_id: str, filename: str) -> None:
        """
        P0-4: 文档成功入库后刷新混合检索索引。

        核心原则：入库成功 ≠ 可检索成功，两个状态分清楚。
        - 刷新成功 → index_status = 'ok'
        - 刷新失败 → index_status = 'refresh_failed' + error_message，
          但 document 处理状态保持 completed（资料确实已入库）。
        """
        try:
            from knowledge.hybrid_rag_engine import refresh_index_after_ingest
            refresh_index_after_ingest()
            self._set_index_status(doc_id, "ok", None)
            logger.info(f"✓ 索引已刷新（doc_id={doc_id}）")
        except Exception as e:
            logger.exception(f"✗ 索引刷新失败（doc_id={doc_id}），资料已入库但暂不可检索")
            self._set_index_status(
                doc_id,
                "refresh_failed",
                f"索引刷新失败，资料已入库但暂不可检索: {e}",
            )

    def _set_index_status(self, doc_id: str, index_status: str, error_message: str) -> None:
        """更新 index_status（不改 document 处理状态）"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if error_message is not None:
                cursor.execute(
                    "UPDATE document_processing_status SET index_status = ?, error_message = ? WHERE doc_id = ?",
                    (index_status, error_message, doc_id),
                )
            else:
                cursor.execute(
                    "UPDATE document_processing_status SET index_status = ? WHERE doc_id = ?",
                    (index_status, doc_id),
                )
            conn.commit()
        except Exception:
            logger.exception("更新 index_status 失败")
            conn.rollback()
        finally:
            conn.close()

    def get_status(self, doc_id: str) -> Dict[str, Any]:
        """
        查询处理状态

        Args:
            doc_id: 文档ID

        Returns:
            状态信息
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT status, progress, total_blocks, error_message, started_at, completed_at, index_status
                FROM document_processing_status
                WHERE doc_id = ?
            """, (doc_id,))

            row = cursor.fetchone()
            if not row:
                return {"status": "not_found"}

            return {
                "status": row[0],
                "progress": row[1],
                "total_blocks": row[2],
                "error_message": row[3],
                "started_at": row[4],
                "completed_at": row[5],
                "index_status": row[6],
            }

        finally:
            conn.close()


# 全局单例
_processing_queue = DocumentProcessingQueue()


def get_processing_queue() -> DocumentProcessingQueue:
    """获取处理队列单例"""
    return _processing_queue


def start_processing_queue():
    """启动处理队列"""
    _processing_queue.start()


def stop_processing_queue():
    """停止处理队列"""
    _processing_queue.stop()
