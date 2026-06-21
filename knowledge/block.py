"""
文档块（Block）结构定义

统一的内部数据结构，所有格式解析器输出Block列表
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import json


@dataclass
class Block:
    """文档块 - 统一的内部结构"""

    type: str  # 块类型: title, heading, paragraph, table, image
    text: str  # 文本内容
    page: Optional[int] = None  # 页码（PDF）或行号（Word/MD）
    source_file: Optional[str] = None  # 源文件名
    section_path: Optional[str] = None  # 章节路径: "第1章/1.1节"
    metadata: Optional[Dict[str, Any]] = None  # 额外元数据: 坐标/置信度/样式

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Block":
        """从字典创建Block"""
        return cls(**data)
