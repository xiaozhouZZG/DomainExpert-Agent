"""
文档清洗器

自动识别并删除页眉/页脚/页码，修复跨页断行，Unicode规整
"""
import re
import unicodedata
from typing import List, Dict, Set, Tuple
from collections import Counter
import logging

logger = logging.getLogger(__name__)


class DocumentCleaner:
    """文档清洗器"""

    @staticmethod
    def normalize_unicode(text: str) -> str:
        """
        Unicode规整化

        - 统一全角/半角
        - 去除控制符
        - NFC标准化
        """
        # NFC标准化
        text = unicodedata.normalize('NFC', text)

        # 去除控制符（保留换行、制表符）
        text = ''.join(char for char in text if not unicodedata.category(char).startswith('C') or char in '\n\t')

        # 统一空白字符
        text = re.sub(r'[  -​  　]', ' ', text)  # 各种空格 → 普通空格
        text = re.sub(r'[ \t]+', ' ', text)  # 多个空格 → 单个空格

        return text.strip()

    @staticmethod
    def detect_repeating_elements(pages: List[str], position: str = 'top') -> Set[str]:
        """
        检测跨页重复元素（页眉/页脚）

        原理：如果某段文本在多个页面的相同位置重复出现，且内容相似，则为页眉/页脚

        Args:
            pages: 页面文本列表
            position: 'top'检测页眉，'bottom'检测页脚

        Returns:
            重复元素集合
        """
        if len(pages) < 3:  # 至少需要3页才能判断重复
            return set()

        # 提取每页的前3行（页眉）或后3行（页脚）
        candidates = []
        for page_text in pages:
            lines = [line.strip() for line in page_text.split('\n') if line.strip()]
            if not lines:
                continue

            if position == 'top':
                candidates.append(lines[:3])
            else:  # bottom
                candidates.append(lines[-3:])

        # 统计每行的出现次数
        line_counts: Dict[str, int] = Counter()
        for page_lines in candidates:
            for line in page_lines:
                # 忽略纯数字（可能是页码，单独处理）
                if not re.match(r'^\d+$', line):
                    line_counts[line] += 1

        # 如果某行在50%以上页面出现，认为是页眉/页脚
        threshold = len(pages) * 0.5
        repeating = {line for line, count in line_counts.items() if count >= threshold}

        return repeating

    @staticmethod
    def detect_page_numbers(pages: List[str]) -> Set[str]:
        """
        检测页码模式

        常见模式：
        - 纯数字：1, 2, 3
        - 带装饰：- 1 -, 第1页
        - 罗马数字：i, ii, iii
        """
        page_number_patterns = [
            r'^-?\s*\d+\s*-?$',  # - 1 -, 1
            r'^第\s*\d+\s*页$',  # 第1页
            r'^\d+\s*/\s*\d+$',  # 1/10
            r'^[ivxlcdm]+$',  # 罗马数字（小写）
            r'^[IVXLCDM]+$',  # 罗马数字（大写）
        ]

        page_numbers = set()
        for page_text in pages:
            lines = [line.strip() for line in page_text.split('\n') if line.strip()]

            # 检查前2行和后2行
            check_lines = []
            if lines:
                check_lines.extend(lines[:2])
                check_lines.extend(lines[-2:])

            for line in check_lines:
                for pattern in page_number_patterns:
                    if re.match(pattern, line, re.IGNORECASE):
                        page_numbers.add(line)
                        break

        return page_numbers

    @staticmethod
    def remove_headers_footers(pages: List[str]) -> List[str]:
        """
        删除页眉和页脚

        Args:
            pages: 页面文本列表

        Returns:
            清洗后的页面列表
        """
        if len(pages) < 2:
            return pages

        # 检测页眉、页脚、页码
        headers = DocumentCleaner.detect_repeating_elements(pages, position='top')
        footers = DocumentCleaner.detect_repeating_elements(pages, position='bottom')
        page_numbers = DocumentCleaner.detect_page_numbers(pages)

        # 合并所有需要删除的模式
        patterns_to_remove = headers | footers | page_numbers

        if not patterns_to_remove:
            logger.info("未检测到页眉/页脚")
            return pages

        logger.info(f"检测到 {len(patterns_to_remove)} 个重复元素（页眉/页脚/页码）")

        # 从每页删除这些模式
        cleaned_pages = []
        for page_text in pages:
            lines = page_text.split('\n')
            cleaned_lines = [
                line for line in lines
                if line.strip() not in patterns_to_remove
            ]
            cleaned_pages.append('\n'.join(cleaned_lines))

        return cleaned_pages

    @staticmethod
    def fix_line_breaks(text: str) -> str:
        """
        修复跨页断行

        规则：
        - 如果行尾是完整句子（。！？），保留换行
        - 如果行尾是单词/汉字中间，合并到下一行
        """
        lines = text.split('\n')
        fixed_lines = []

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if not line:
                i += 1
                continue

            # 检查是否是完整句子结尾
            if line[-1] in '。！？.!?；;':
                fixed_lines.append(line)
                i += 1
            elif i + 1 < len(lines):
                # 合并到下一行
                next_line = lines[i + 1].strip()
                if next_line:
                    fixed_lines.append(line + next_line)
                    i += 2
                else:
                    fixed_lines.append(line)
                    i += 1
            else:
                fixed_lines.append(line)
                i += 1

        return '\n'.join(fixed_lines)

    @staticmethod
    def clean_document(pages: List[str]) -> str:
        """
        完整清洗流程

        Args:
            pages: 页面文本列表

        Returns:
            清洗后的完整文本
        """
        # 1. 删除页眉/页脚/页码
        cleaned_pages = DocumentCleaner.remove_headers_footers(pages)

        # 2. Unicode规整
        cleaned_pages = [DocumentCleaner.normalize_unicode(page) for page in cleaned_pages]

        # 3. 合并所有页
        full_text = '\n\n'.join(cleaned_pages)

        # 4. 修复跨页断行
        full_text = DocumentCleaner.fix_line_breaks(full_text)

        return full_text
