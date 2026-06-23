"""数据库模块初始化"""
from .connection import get_db_connection, ensure_tables

__all__ = [
    "get_db_connection",
    "ensure_tables",
]
