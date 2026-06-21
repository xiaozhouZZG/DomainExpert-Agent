"""
结构感知分块器

按标题层级切块，保持表格完整性
"""
import logging
from typing import List

from knowledge.block import Block

logger = logging.getLogger(__name__)


class StructureAwareChunker:
    """结构感知分块器"""

    def __init__(self, max_chunk_size: int = 800):
        """
        初始化

        Args:
            max_chunk_size: 最大块大小（字符数）
        """
        self.max_chunk_size = max_chunk_size

    def chunk_blocks(self, blocks: List[Block]) -> List[Block]:
        """
        对Block列表进行结构感知分块

        规则：
        1. 标题（title/heading）单独成块
        2. 表格（table）整块保留，不切分
        3. 段落（paragraph）按大小合并，不超过max_chunk_size
        4. 保持section_path一致性

        Args:
            blocks: 输入Block列表

        Returns:
            分块后的Block列表
        """
        chunked_blocks = []
        current_chunk_text = []
        current_section = None
        current_source = None
        current_page = None

        for block in blocks:
            # 标题：单独成块
            if block.type in ['title', 'heading']:
                # 先处理积累的段落
                if current_chunk_text:
                    chunked_blocks.append(Block(
                        type='paragraph',
                        text='\n\n'.join(current_chunk_text),
                        page=current_page,
                        source_file=current_source,
                        section_path=current_section,
                        metadata={}
                    ))
                    current_chunk_text = []

                # 标题单独成块
                chunked_blocks.append(block)
                current_section = block.section_path
                current_source = block.source_file
                current_page = block.page

            # 表格：整块保留
            elif block.type == 'table':
                # 先处理积累的段落
                if current_chunk_text:
                    chunked_blocks.append(Block(
                        type='paragraph',
                        text='\n\n'.join(current_chunk_text),
                        page=current_page,
                        source_file=current_source,
                        section_path=current_section,
                        metadata={}
                    ))
                    current_chunk_text = []

                # 表格整块保留
                chunked_blocks.append(block)

            # 段落：合并，但不超过max_chunk_size
            elif block.type == 'paragraph':
                # 如果当前块会超出大小限制，先flush
                if current_chunk_text and sum(len(t) for t in current_chunk_text) + len(block.text) > self.max_chunk_size:
                    chunked_blocks.append(Block(
                        type='paragraph',
                        text='\n\n'.join(current_chunk_text),
                        page=current_page,
                        source_file=current_source,
                        section_path=current_section,
                        metadata={}
                    ))
                    current_chunk_text = []

                # 添加当前段落
                current_chunk_text.append(block.text)
                current_section = block.section_path
                current_source = block.source_file
                current_page = block.page

        # 处理最后的积累段落
        if current_chunk_text:
            chunked_blocks.append(Block(
                type='paragraph',
                text='\n\n'.join(current_chunk_text),
                page=current_page,
                source_file=current_source,
                section_path=current_section,
                metadata={}
            ))

        logger.info(f"结构感知分块完成: {len(blocks)} 个块 → {len(chunked_blocks)} 个块")
        return chunked_blocks
