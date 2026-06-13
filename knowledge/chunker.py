"""文本分块器"""
from typing import List, Dict, Any


class SimpleChunker:
    """简单文本分块器（类接口）"""

    def __init__(self, chunk_size: int = 512, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_text(self, text: str) -> List[str]:
        """
        分块文本，返回字符串列表

        Args:
            text: 原始文本

        Returns:
            分块文本列表
        """
        chunks_dict = chunk_text(text, self.chunk_size, self.overlap)
        return [chunk["text"] for chunk in chunks_dict]


def chunk_text(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    separators: List[str] = None
) -> List[Dict[str, Any]]:
    """
    递归字符分块器(RecursiveCharacterTextSplitter 纯 Python 实现)

    参数:
        text: 原始文本
        chunk_size: 分块大小(字符数)
        chunk_overlap: 重叠大小(注意: 当前实现仅在最后字符级兜底分割时生效)
        separators: 分隔符列表(默认: 段落 → 句子 → 字符)

    返回:
        分块列表 [{"text": "...", "start": 0, "end": 100}, ...]

    注意:
    - 优先按语义边界(段落/句子)分割，此时不保证精确 chunk_overlap
    - 仅在强制字符分割时才使用 overlap 参数
    - 如需精确 overlap，可在后处理阶段滑动窗口重构
    """
    if separators is None:
        separators = ["\n\n", "\n", "。", "；", "！", "？", " ", ""]

    chunks = []
    _recursive_split(text, chunk_size, chunk_overlap, separators, chunks)

    return [{"text": chunk, "start": 0, "end": len(chunk)} for chunk in chunks]


def _recursive_split(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    separators: List[str],
    result: List[str]
):
    """递归分割文本"""
    if len(text) <= chunk_size:
        if text.strip():
            result.append(text.strip())
        return

    # 尝试用当前分隔符分割
    sep = separators[0] if separators else ""

    if sep:
        parts = text.split(sep)
    else:
        # 最后一层：按字符数强制分割，带 overlap
        parts = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size - chunk_overlap)]
        result.extend([p.strip() for p in parts if p.strip()])
        return

    # 合并小片段
    current_chunk = ""
    for part in parts:
        if len(current_chunk) + len(part) + len(sep) <= chunk_size:
            current_chunk += part + sep
        else:
            if current_chunk.strip():
                result.append(current_chunk.strip())

            # 如果单个 part 仍然太大，递归分割
            if len(part) > chunk_size:
                _recursive_split(part, chunk_size, chunk_overlap, separators[1:], result)
            else:
                current_chunk = part + sep

    if current_chunk.strip():
        result.append(current_chunk.strip())
