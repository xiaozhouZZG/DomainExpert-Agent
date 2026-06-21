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

    def submit_task(self, file_path: str, filename: str, file_type: str) -> str:
        """
        提交处理任务

        Args:
            file_path: 文件路径
            filename: 文件名
            file_type: 文件类型

        Returns:
            doc_id
        """
        doc_id = str(uuid.uuid4())

        # 创建处理状态记录
        self._create_processing_status(doc_id, filename)

        # 提交任务
        task = ProcessingTask(doc_id, file_path, filename, file_type)
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
                        task.file_type
                    )
                    logger.info(f"✓ 任务完成: {task.filename} - {result['chunks']} 个块")

                except Exception as e:
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
                SELECT status, progress, total_blocks, error_message, started_at, completed_at
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
