"""问候接口 - AI 生成开场白"""
import logging
from fastapi import APIRouter
from core.config_manager import ConfigManager
from core.llm_client import create_llm_client

router = APIRouter()
logger = logging.getLogger(__name__)

# 固定兜底问候语
FALLBACK_GREETING = "您好，我是智能客服，很高兴为您服务~ 请问有什么可以帮您？"


@router.get("/api/greeting")
async def get_greeting():
    """
    获取 AI 生成的问候语

    Returns:
        {"greeting": str}  # AI 生成的问候语，失败时返回固定兜底
    """
    try:
        # 获取 LLM 配置
        llm_config = ConfigManager.get_llm_config()

        if not llm_config.get("base_url") or not llm_config.get("api_key"):
            logger.warning("LLM 配置不完整，使用固定问候")
            return {"greeting": FALLBACK_GREETING}

        # 创建 LLM 客户端
        client = create_llm_client(
            protocol=llm_config["protocol"],
            base_url=llm_config["base_url"],
            api_key=llm_config["api_key"],
            model=llm_config["model"]
        )

        # 生成问候语
        messages = [
            {
                "role": "system",
                "content": "你是一名专业的智能客服，请用简短、友好、自然的语气生成一句开场问候（1~2句话，中文，引导用户说明需求）。直接输出问候语，不要额外解释。"
            },
            {
                "role": "user",
                "content": "生成一句客服开场问候"
            }
        ]

        logger.info("[问候接口] 调用 LLM 生成问候语")
        greeting = await client.chat(messages, temperature=0.8)

        # LLM 返回空，使用兜底
        if not greeting or not greeting.strip():
            logger.warning("[问候接口] LLM 返回空，使用固定问候")
            return {"greeting": FALLBACK_GREETING}

        # 清理多余的引号
        greeting = greeting.strip().strip('"').strip("'")

        logger.info(f"[问候接口] 生成问候: {greeting}")
        return {"greeting": greeting}

    except Exception as e:
        logger.error(f"[问候接口] 生成失败: {e}", exc_info=True)
        return {"greeting": FALLBACK_GREETING}
