"""
文档入库处理器

核心功能：
1. 解析文档 → Block列表
2. 结构感知分块
3. 生成embedding（BGE）
4. 存入数据库（chunks表 + BM25索引）
"""
import os
import json
import uuid
import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Any

from knowledge.structured_loader import StructuredDocumentLoader
from knowledge.structure_aware_chunker import StructureAwareChunker
from knowledge.embedder import BGEEmbedder
from knowledge.block import Block
from database.connection import get_db_connection

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """文档入库处理器"""

    def __init__(self):
        self.loader = StructuredDocumentLoader()
        self.chunker = StructureAwareChunker(max_chunk_size=800)
        self.embedder = None  # 延迟加载

    def _ensure_embedder(self):
        """延迟加载embedder（避免启动时加载模型）"""
        if self.embedder is None:
            logger.info("加载BGE向量模型...")
            self.embedder = BGEEmbedder()

    def process_document(
        self,
        file_path: str,
        doc_id: str,
        filename: str,
        file_type: str
    ) -> Dict[str, Any]:
        """
        处理单个文档

        Args:
            file_path: 文件路径
            doc_id: 文档ID
            filename: 文件名
            file_type: 文件类型

        Returns:
            处理结果统计
        """
        try:
            # 更新状态：处理中
            self._update_processing_status(doc_id, "processing", 0, 0, None)

            # 1. 解析文档 → Block列表
            logger.info(f"[1/5] 解析文档: {filename}")
            blocks = self.loader.load_document(file_path, file_type)

            if not blocks:
                raise ValueError("文档解析失败，未提取到内容")

            # 2. 结构感知分块
            logger.info(f"[2/5] 结构感知分块: {len(blocks)} 个原始块")
            chunked_blocks = self.chunker.chunk_blocks(blocks)

            # 3. 生成embedding
            logger.info(f"[3/5] 生成向量embedding: {len(chunked_blocks)} 个块")
            self._ensure_embedder()

            texts = [block.text for block in chunked_blocks]
            embeddings = self.embedder.embed_batch(texts)

            # 4. 存入数据库
            logger.info(f"[4/5] 存入数据库: {len(chunked_blocks)} 个块")
            self._save_to_database(doc_id, filename, chunked_blocks, embeddings)

            # 5. 更新状态：完成
            self._update_processing_status(
                doc_id, "completed", len(chunked_blocks), len(chunked_blocks), None
            )

            logger.info(f"[5/5] 文档处理完成: {filename}")

            return {
                "status": "success",
                "doc_id": doc_id,
                "filename": filename,
                "total_blocks": len(blocks),
                "chunks": len(chunked_blocks),
                "embeddings_generated": len(embeddings),
            }

        except Exception as e:
            logger.exception(f"文档处理失败: {filename}")
            self._update_processing_status(doc_id, "failed", 0, 0, str(e))
            raise

    def _save_to_database(
        self,
        doc_id: str,
        filename: str,
        blocks: List[Block],
        embeddings: List[Any]
    ):
        """
        保存到数据库

        Args:
            doc_id: 文档ID
            filename: 文件名
            blocks: Block列表
            embeddings: embedding列表
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # 插入documents记录
            cursor.execute("""
                INSERT OR REPLACE INTO documents (doc_id, file_path, title, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
            """, (
                doc_id,
                filename,
                filename,
                datetime.now().isoformat(),
                json.dumps({"chunks_count": len(blocks)}, ensure_ascii=False)
            ))

            # 插入chunks记录（带embedding）
            for idx, (block, embedding) in enumerate(zip(blocks, embeddings)):
                chunk_id = f"{doc_id}_chunk_{idx}"

                # 序列化embedding为BLOB
                embedding_blob = embedding.tobytes()

                # metadata转JSON
                metadata_json = json.dumps(block.metadata or {}, ensure_ascii=False)

                cursor.execute("""
                    INSERT OR REPLACE INTO chunks (
                        chunk_id, doc_id, chunk_index, text, embedding,
                        block_type, page, source_file, section_path, metadata,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chunk_id,
                    doc_id,
                    idx,
                    block.text,
                    embedding_blob,
                    block.type,
                    block.page,
                    block.source_file,
                    block.section_path,
                    metadata_json,
                    datetime.now().isoformat()
                ))

            conn.commit()
            logger.info(f"✓ 数据库保存成功: {len(blocks)} 个块")

        except Exception as e:
            conn.rollback()
            logger.exception("数据库保存失败")
            raise
        finally:
            conn.close()

    def _update_processing_status(
        self,
        doc_id: str,
        status: str,
        progress: int,
        total_blocks: int,
        error_message: str = None
    ):
        """
        更新处理状态

        Args:
            doc_id: 文档ID
            status: 状态（queued/processing/completed/failed）
            progress: 当前进度
            total_blocks: 总块数
            error_message: 错误信息
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            now = datetime.now().isoformat()

            if status == "processing":
                cursor.execute("""
                    UPDATE document_processing_status
                    SET status = ?, progress = ?, total_blocks = ?, started_at = ?
                    WHERE doc_id = ?
                """, (status, progress, total_blocks, now, doc_id))
            elif status == "completed":
                cursor.execute("""
                    UPDATE document_processing_status
                    SET status = ?, progress = ?, total_blocks = ?, completed_at = ?
                    WHERE doc_id = ?
                """, (status, progress, total_blocks, now, doc_id))
            elif status == "failed":
                cursor.execute("""
                    UPDATE document_processing_status
                    SET status = ?, error_message = ?, completed_at = ?
                    WHERE doc_id = ?
                """, (status, error_message, now, doc_id))

            conn.commit()
        except Exception as e:
            logger.exception("更新状态失败")
            conn.rollback()
        finally:
            conn.close()

    def reprocess_document(self, doc_id: str) -> Dict[str, Any]:
        """
        重新处理已有文档（用于旧文档升级）

        Args:
            doc_id: 文档ID

        Returns:
            处理结果
        """
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # 获取原始文档信息
            cursor.execute("SELECT file_path, title FROM documents WHERE doc_id = ?", (doc_id,))
            row = cursor.fetchone()

            if not row:
                raise ValueError(f"文档不存在: {doc_id}")

            file_path, title = row

            # 删除旧的chunks
            cursor.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.commit()

            # 重新处理（假设文件还在）
            if os.path.exists(file_path):
                _, ext = os.path.splitext(file_path)
                file_type = ext.lower().lstrip('.')
                return self.process_document(file_path, doc_id, title, file_type)
            else:
                raise FileNotFoundError(f"源文件不存在: {file_path}")

        finally:
            conn.close()
