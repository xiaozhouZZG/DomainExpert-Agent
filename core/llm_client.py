"""
LLM 多协议适配层 - 核心兼容性增强版

支持 OpenAI、Anthropic、Gemini 三种协议
【关键设计】任何异常都不抛出，返回空字符串 "" 信号，由上层兜底
"""
import httpx
import logging
import json
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class LLMClient:
    """统一的 LLM 客户端 - 永不抛异常版"""

    def __init__(self, protocol: str, base_url: str, api_key: str, model: str):
        """
        初始化客户端

        Args:
            protocol: 协议类型 (openai/anthropic/gemini)
            base_url: API 基础地址
            api_key: API Key
            model: 模型名称
        """
        self.protocol = protocol.lower() if protocol else "openai"

        # base_url 规范化：去除尾部 /，处理空值
        if not base_url or not base_url.strip():
            logger.error("base_url 为空，LLM 配置错误")
            self.base_url = ""
            self.config_valid = False
        else:
            self.base_url = base_url.rstrip('/')
            self.config_valid = True

        self.api_key = api_key if api_key else ""
        self.model = model if model else ""

    async def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        """
        统一的对话接口 - 永不抛异常，失败返回 ""

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            temperature: 温度参数

        Returns:
            str: 模型回复内容，失败返回 ""
        """
        # 配置校验
        if not self.config_valid or not self.base_url:
            logger.error("LLM 配置无效（base_url 为空），无法调用")
            return ""

        if not self.api_key:
            logger.error("LLM API Key 为空，无法调用")
            return ""

        try:
            if self.protocol == "openai":
                return await self._chat_openai(messages, temperature)
            elif self.protocol == "anthropic":
                return await self._chat_anthropic(messages, temperature)
            elif self.protocol == "gemini":
                return await self._chat_gemini(messages, temperature)
            else:
                logger.error(f"不支持的协议: {self.protocol}")
                return ""
        except Exception as e:
            logger.error(f"LLM 调用顶层异常: {e}", exc_info=True)
            return ""

    async def list_models(self) -> List[str]:
        """获取模型列表 - 失败返回空列表"""
        if not self.config_valid:
            return []

        try:
            if self.protocol == "openai":
                return await self._list_models_openai()
            elif self.protocol == "anthropic":
                return await self._list_models_anthropic()
            elif self.protocol == "gemini":
                return await self._list_models_gemini()
            else:
                return []
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
            return []

    # ============ OpenAI 协议 ============

    async def _chat_openai(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """
        OpenAI 协议对话 - 兼容标准 JSON 和 SSE 流式响应

        失败返回 ""，不抛异常
        """
        try:
            # 避免重复拼接 /v1
            if "/chat/completions" in self.base_url:
                url = self.base_url
            elif self.base_url.endswith("/v1"):
                url = f"{self.base_url}/chat/completions"
            else:
                url = f"{self.base_url}/chat/completions"

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }

            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "stream": False  # 强制非流式
            }

            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                response = await client.post(url, json=payload, headers=headers)

                # HTTP 错误处理
                if response.status_code != 200:
                    logger.error(f"[OpenAI协议] HTTP {response.status_code}: {response.text[:500]}")
                    return ""

                response_text = response.text
                content_type = response.headers.get("content-type", "")

                # 判断是否为 SSE 流式响应
                is_sse = (
                    "text/event-stream" in content_type or
                    response_text.strip().startswith("data:")
                )

                if is_sse:
                    logger.warning("[OpenAI协议] 检测到 SSE 流式响应，解析中...")
                    return self._parse_sse_response(response_text)
                else:
                    # 标准 JSON 响应
                    return self._parse_json_response(response_text)

        except httpx.TimeoutException:
            logger.error("[OpenAI协议] 请求超时（120s）")
            return ""
        except httpx.RequestError as e:
            logger.error(f"[OpenAI协议] 网络请求错误: {e}")
            return ""
        except Exception as e:
            logger.error(f"[OpenAI协议] 未预期异常: {e}", exc_info=True)
            return ""

    def _parse_sse_response(self, response_text: str) -> str:
        """
        解析 SSE 流式响应

        格式示例：
        data: {"choices":[{"delta":{"content":"你好"}}],"completion_tokens":0}
        data: {"choices":[{"delta":{"content":"世界"}}],"completion_tokens":2}
        data: [DONE]
        """
        try:
            lines = response_text.strip().split('\n')
            content_chunks = []

            for line in lines:
                if not line.startswith("data: "):
                    continue

                json_str = line[6:].strip()  # 去掉 "data: " 前缀

                if json_str == "[DONE]":
                    break

                try:
                    chunk_data = json.loads(json_str)

                    # 跳过空 choices
                    if "choices" not in chunk_data or not chunk_data["choices"]:
                        continue

                    # 跳过 completion_tokens=0 的空块
                    if chunk_data.get("completion_tokens") == 0 and not chunk_data.get("choices")[0].get("delta", {}).get("content"):
                        continue

                    choice = chunk_data["choices"][0]

                    # 优先取 delta.content（流式）
                    if "delta" in choice and "content" in choice["delta"]:
                        content = choice["delta"]["content"]
                        if content:
                            content_chunks.append(content)
                    # 回退取 message.content（完整块）
                    elif "message" in choice and "content" in choice["message"]:
                        content = choice["message"]["content"]
                        if content:
                            content_chunks.append(content)

                except json.JSONDecodeError:
                    continue  # 跳过无效 JSON 行

            if content_chunks:
                final_content = "".join(content_chunks)
                logger.info(f"[OpenAI协议] SSE 解析成功，内容长度: {len(final_content)}")
                return final_content
            else:
                logger.warning("[OpenAI协议] SSE 响应无有效内容")
                return ""

        except Exception as e:
            logger.error(f"[OpenAI协议] SSE 解析异常: {e}")
            return ""

    def _parse_json_response(self, response_text: str) -> str:
        """解析标准 JSON 响应"""
        try:
            data = json.loads(response_text)

            # 检查 choices
            if "choices" not in data or not data["choices"]:
                logger.warning("[OpenAI协议] choices 为空")
                return ""

            # 检查 completion_tokens
            if data.get("usage", {}).get("completion_tokens") == 0:
                logger.warning("[OpenAI协议] completion_tokens=0，可能无内容")

            content = data["choices"][0].get("message", {}).get("content", "")

            if not content or not content.strip():
                logger.warning("[OpenAI协议] 内容为空字符串")
                return ""

            return content

        except json.JSONDecodeError as e:
            logger.error(f"[OpenAI协议] JSON 解析失败: {e}")
            logger.error(f"响应前 500 字符: {response_text[:500]}")
            return ""
        except Exception as e:
            logger.error(f"[OpenAI协议] 响应解析异常: {e}")
            return ""

    async def _list_models_openai(self) -> List[str]:
        """OpenAI 协议获取模型列表"""
        try:
            url = f"{self.base_url}/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 404:
                    return []

                if response.status_code != 200:
                    return []

                data = response.json()
                if "data" in data:
                    return [m["id"] for m in data["data"] if "id" in m]
                return []
        except:
            return []

    # ============ Anthropic 协议 ============

    async def _chat_anthropic(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """
        Anthropic 协议对话 - 基于 base_url 拼接，不硬编码官方域名

        失败返回 ""
        """
        try:
            url = f"{self.base_url}/v1/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }

            # 提取 system 消息
            system_content = None
            user_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_content = msg["content"]
                else:
                    user_messages.append(msg)

            payload = {
                "model": self.model,
                "max_tokens": 1024,  # 必需
                "messages": user_messages,
                "temperature": temperature
            }

            if system_content:
                payload["system"] = system_content

            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code != 200:
                    logger.error(f"[Anthropic协议] HTTP {response.status_code}: {response.text[:500]}")
                    return ""

                data = response.json()

                if "content" not in data or not data["content"]:
                    logger.warning("[Anthropic协议] content 为空")
                    return ""

                content = data["content"][0].get("text", "")

                if not content or not content.strip():
                    logger.warning("[Anthropic协议] 内容为空字符串")
                    return ""

                return content

        except httpx.TimeoutException:
            logger.error("[Anthropic协议] 请求超时")
            return ""
        except httpx.RequestError as e:
            logger.error(f"[Anthropic协议] 网络请求错误: {e}")
            return ""
        except Exception as e:
            logger.error(f"[Anthropic协议] 未预期异常: {e}", exc_info=True)
            return ""

    async def _list_models_anthropic(self) -> List[str]:
        """Anthropic 协议获取模型列表"""
        try:
            url = f"{self.base_url}/v1/models"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01"
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 404:
                    # 返回预设模型列表
                    return [
                        "claude-3-5-sonnet-20241022",
                        "claude-3-5-haiku-20241022",
                        "claude-3-opus-20240229"
                    ]

                if response.status_code != 200:
                    return []

                data = response.json()
                if "data" in data:
                    return [m["id"] for m in data["data"] if "id" in m]
                return []
        except:
            return []

    # ============ Gemini 协议 ============

    async def _chat_gemini(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """
        Gemini 原生协议对话 - 基于 base_url 拼接

        失败返回 ""
        """
        try:
            url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
            params = {"key": self.api_key}
            headers = {"Content-Type": "application/json"}

            # 提取 system 消息
            system_content = None
            user_contents = []

            for msg in messages:
                if msg["role"] == "system":
                    system_content = msg["content"]
                else:
                    role = "user" if msg["role"] == "user" else "model"
                    user_contents.append({
                        "role": role,
                        "parts": [{"text": msg["content"]}]
                    })

            payload = {
                "contents": user_contents,
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": 1000
                }
            }

            if system_content:
                payload["systemInstruction"] = {
                    "parts": [{"text": system_content}]
                }

            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                response = await client.post(url, params=params, json=payload, headers=headers)

                if response.status_code != 200:
                    logger.error(f"[Gemini协议] HTTP {response.status_code}: {response.text[:500]}")
                    return ""

                data = response.json()

                if "candidates" not in data or not data["candidates"]:
                    logger.warning("[Gemini协议] candidates 为空")
                    return ""

                content = data["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text", "")

                if not content or not content.strip():
                    logger.warning("[Gemini协议] 内容为空字符串")
                    return ""

                return content

        except httpx.TimeoutException:
            logger.error("[Gemini协议] 请求超时")
            return ""
        except httpx.RequestError as e:
            logger.error(f"[Gemini协议] 网络请求错误: {e}")
            return ""
        except Exception as e:
            logger.error(f"[Gemini协议] 未预期异常: {e}", exc_info=True)
            return ""

    async def _list_models_gemini(self) -> List[str]:
        """Gemini 原生协议获取模型列表"""
        try:
            url = f"{self.base_url}/v1beta/models"
            params = {"key": self.api_key}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)

                if response.status_code != 200:
                    return []

                data = response.json()
                if "models" in data:
                    return [m["name"].replace("models/", "") for m in data["models"] if "name" in m]
                return []
        except:
            return []


# ============ 工厂函数 ============

def create_llm_client(protocol: str, base_url: str, api_key: str, model: str) -> LLMClient:
    """
    创建 LLM 客户端 - 永不抛异常版

    Args:
        protocol: 协议类型 (openai/anthropic/gemini)
        base_url: API 基础地址
        api_key: API Key
        model: 模型名称

    Returns:
        LLMClient 实例（配置无效时也返回，但调用会返回空字符串）
    """
    return LLMClient(protocol, base_url, api_key, model)
