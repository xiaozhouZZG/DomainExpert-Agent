"""
配置管理器

负责 LLM 配置的读取、保存和加密存储
"""
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from database.connection import get_db_connection

logger = logging.getLogger(__name__)


class ConfigManager:
    """配置管理器"""

    @staticmethod
    def get_config(key: str) -> Optional[str]:
        """获取配置项"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    @staticmethod
    def set_config(key: str, value: str):
        """设置配置项"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO system_config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
            """, (key, value, datetime.now().isoformat()))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_llm_config() -> Dict[str, Any]:
        """
        获取 LLM 配置

        优先级：数据库（非空） > config.settings（.env） > 默认值
        """
        from config import settings

        # 从数据库读取
        db_base_url = ConfigManager.get_config("llm_base_url")
        db_api_key = ConfigManager.get_config("llm_api_key")
        db_model = ConfigManager.get_config("llm_model")
        db_provider = ConfigManager.get_config("llm_provider")
        db_protocol = ConfigManager.get_config("llm_protocol")

        # 优先级：数据库有值且非空 > settings对象（已加载.env） > 默认值
        base_url = db_base_url if db_base_url else settings.llm_base_url
        api_key = db_api_key if db_api_key else settings.llm_api_key
        model = db_model if db_model else settings.llm_model
        protocol = db_protocol if db_protocol else settings.llm_protocol
        provider = db_provider if db_provider else "custom"

        # 记录配置来源
        if not db_base_url:
            logger.info("LLM base_url 从 settings 加载")
        if not db_api_key:
            logger.info("LLM api_key 从 settings 加载")
        if not db_model:
            logger.info("LLM model 从 settings 加载")

        return {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "provider": provider,
            "protocol": protocol
        }

    @staticmethod
    def save_llm_config(base_url: str, api_key: str, model: str, provider: str = "custom", protocol: str = "openai"):
        """保存 LLM 配置"""
        ConfigManager.set_config("llm_base_url", base_url)
        ConfigManager.set_config("llm_api_key", api_key)
        ConfigManager.set_config("llm_model", model)
        ConfigManager.set_config("llm_provider", provider)
        ConfigManager.set_config("llm_protocol", protocol)
        logger.info(f"LLM 配置已保存: provider={provider}, protocol={protocol}, model={model}")

    @staticmethod
    def mask_api_key(api_key: str) -> str:
        """脱敏 API Key"""
        if not api_key:
            return ""
        if len(api_key) <= 8:
            return "****"
        return api_key[:4] + "****" + api_key[-4:]
