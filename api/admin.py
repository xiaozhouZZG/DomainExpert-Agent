"""
后台管理 API

提供仪表盘统计、知识库管理、对话记录查询等功能
"""
import os
import json
import logging
import tempfile
import httpx
from datetime import datetime
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Header, UploadFile, File
from pydantic import BaseModel

from database.connection import get_db_connection
from knowledge.hybrid_rag_engine import get_hybrid_engine
from knowledge.document_loader import DocumentLoader
from knowledge.chunker import SimpleChunker
from observability.metrics import get_metrics
from core.config_manager import ConfigManager
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 支持的文件类型和大小限制
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.md', '.txt'}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


# ============ 认证 ============

# ============ 仪表盘统计 ============

@router.get("/dashboard")
async def get_dashboard_stats():
    """获取仪表盘统计数据（全部从真实数据聚合，无假数据）"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. 对话总数（从 conversations 表统计）
        cursor.execute("SELECT COUNT(DISTINCT session_id) FROM conversations")
        total_conversations = cursor.fetchone()[0] or 0

        # 2. 总消息数
        cursor.execute("SELECT COUNT(*) FROM conversations")
        total_messages = cursor.fetchone()[0] or 0

        # 3. 今日请求数（从 request_logs 表统计）
        cursor.execute("""
            SELECT COUNT(*) FROM request_logs
            WHERE DATE(created_at) = DATE('now')
        """)
        today_requests = cursor.fetchone()[0] or 0

        # 4. 各 Agent 调用次数（从 request_logs 表按 agent 分组）
        cursor.execute("""
            SELECT agent, COUNT(*) as count
            FROM request_logs
            GROUP BY agent
            ORDER BY count DESC
        """)
        agent_calls = [{"agent": row[0], "count": row[1]} for row in cursor.fetchall()]

        # 5. 引擎模式分布（从 request_logs 表按 engine 分组）
        cursor.execute("""
            SELECT engine, COUNT(*) as count
            FROM request_logs
            GROUP BY engine
            ORDER BY count DESC
        """)
        engine_distribution = [{"engine": row[0], "count": row[1]} for row in cursor.fetchall()]

        # 6. 平均响应时长（从 request_logs 表）
        cursor.execute("""
            SELECT AVG(latency_ms) as avg_latency
            FROM request_logs
            WHERE latency_ms IS NOT NULL
        """)
        avg_latency = cursor.fetchone()[0] or 0

        # 7. 知识库统计
        cursor.execute("SELECT COUNT(*) FROM knowledge_base")
        kb_documents = cursor.fetchone()[0] or 0

        # 8. 转人工工单统计
        cursor.execute("SELECT COUNT(*) FROM handoff_tickets")
        total_tickets = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM handoff_tickets WHERE status = '待处理'")
        pending_tickets = cursor.fetchone()[0] or 0

        # 9. 知识库命中率（新增）
        # 今日命中率
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN kb_hit = 1 THEN 1 ELSE 0 END) as hits
            FROM request_logs
            WHERE countable = 1 AND DATE(created_at) = DATE('now')
        """)
        row = cursor.fetchone()
        today_total = row[0] or 0
        today_hits = row[1] or 0
        today_hit_rate = round((today_hits / today_total * 100), 2) if today_total > 0 else None

        # 总体命中率
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN kb_hit = 1 THEN 1 ELSE 0 END) as hits,
                SUM(CASE WHEN kb_hit = 0 THEN 1 ELSE 0 END) as misses
            FROM request_logs
            WHERE countable = 1
        """)
        row = cursor.fetchone()
        total_requests = row[0] or 0
        total_hits = row[1] or 0
        total_misses = row[2] or 0
        overall_hit_rate = round((total_hits / total_requests * 100), 2) if total_requests > 0 else None

        return {
            "conversations": {
                "total": total_conversations,
                "total_messages": total_messages,
                "today_requests": today_requests
            },
            "kb_hit_rate": {
                "today_rate": today_hit_rate,  # None 表示无数据
                "overall_rate": overall_hit_rate,  # None 表示无数据
                "total_hits": total_hits,
                "total_misses": total_misses,
                "total_requests": total_requests
            },
            "agents": agent_calls if agent_calls else [],
            "engines": engine_distribution if engine_distribution else [],
            "performance": {
                "avg_latency_ms": round(avg_latency, 2) if avg_latency else 0,
                "avg_latency_sec": round(avg_latency / 1000, 2) if avg_latency else 0
            },
            "knowledge_base": {
                "total_documents": kb_documents
            },
            "tickets": {
                "total": total_tickets,
                "pending": pending_tickets
            }
        }

    finally:
        conn.close()


# ============ 知识库管理 ============

@router.get("/knowledge/list")
async def list_knowledge_documents():
    """获取知识库文档列表"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 查询documents表（新入库管线使用的表）
        cursor.execute("""
            SELECT d.doc_id, d.title, d.file_path, d.created_at, d.metadata,
                   COUNT(c.id) as chunk_count
            FROM documents d
            LEFT JOIN chunks c ON d.doc_id = c.doc_id
            GROUP BY d.doc_id
            ORDER BY d.created_at DESC
        """)

        documents = []
        for row in cursor.fetchall():
            doc_id, title, file_path, created_at, metadata_str, chunk_count = row

            # 解析metadata
            try:
                metadata = json.loads(metadata_str) if metadata_str else {}
            except:
                metadata = {}

            documents.append({
                "id": doc_id,
                "title": title,
                "source": file_path,  # 兼容前端的source字段
                "created_at": created_at,
                "metadata": metadata,
                "chunks": chunk_count  # 分块数量
            })

        return {"documents": documents, "total": len(documents)}

    finally:
        conn.close()


@router.post("/knowledge/upload")
async def upload_knowledge_document(
    file: UploadFile = File(...),
):
    """上传文档到知识库（异步处理，支持 PDF/Word/Markdown）"""
    from knowledge.processing_queue import get_processing_queue

    # 验证文件扩展名
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {file_ext}。支持的格式: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # 读取文件内容
    content = await file.read()

    # 验证文件大小
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大: {len(content) / 1024 / 1024:.1f}MB，最大限制: {MAX_FILE_SIZE / 1024 / 1024}MB"
        )

    # 保存到临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_file:
        tmp_file.write(content)
        tmp_path = tmp_file.name

    try:
        # 提交异步处理任务
        queue = get_processing_queue()
        doc_id = queue.submit_task(
            file_path=tmp_path,
            filename=file.filename,
            file_type=file_ext.lstrip('.')
        )

        return {
            "status": "queued",
            "doc_id": doc_id,
            "filename": file.filename,
            "message": f"文档已加入处理队列，doc_id: {doc_id}"
        }

    except Exception as e:
        # 清理临时文件
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"提交任务失败: {str(e)}")


class DocumentUpload(BaseModel):
    content: str
    source: str


@router.post("/knowledge/upload-text")
async def upload_text_document(
    doc: DocumentUpload,
):
    """上传纯文本到知识库（兼容旧接口）"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 插入文档
        cursor.execute("""
            INSERT INTO knowledge_base (content, source, created_at)
            VALUES (?, ?, ?)
        """, (doc.content, doc.source, datetime.now().isoformat()))

        conn.commit()
        doc_id = cursor.lastrowid

        # 触发 RAG 引擎重新索引（如果支持）
        rag = get_hybrid_engine()
        if hasattr(rag, 'reindex'):
            rag.reindex()

        return {
            "status": "success",
            "document_id": doc_id,
            "message": "文档已上传并索引"
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")

    finally:
        conn.close()


@router.get("/knowledge/processing-status/{doc_id}")
async def get_processing_status(doc_id: str):
    """查询文档处理状态"""
    from knowledge.processing_queue import get_processing_queue

    queue = get_processing_queue()
    status = queue.get_status(doc_id)

    if status["status"] == "not_found":
        raise HTTPException(status_code=404, detail="文档不存在")

    return status


@router.post("/knowledge/reprocess/{doc_id}")
async def reprocess_document(doc_id: str):
    """重新处理已有文档（用于旧文档升级）"""
    from knowledge.document_processor import DocumentProcessor

    try:
        processor = DocumentProcessor()
        result = processor.reprocess_document(doc_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重新处理失败: {str(e)}")


@router.delete("/knowledge/{doc_id}")
async def delete_knowledge_document(
    doc_id: str,
):
    """删除知识库文档（UUID字符串）"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 先检查文档是否存在
        cursor.execute("SELECT doc_id FROM documents WHERE doc_id = ?", (doc_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

        # 级联删除：按外键依赖顺序 - 先删子表，后删父表
        # 1. 删除处理状态（引用documents的外键）
        cursor.execute("DELETE FROM document_processing_status WHERE doc_id = ?", (doc_id,))

        # 2. 删除chunks（引用documents的外键）
        cursor.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        chunks_deleted = cursor.rowcount

        # 3. 最后删除documents（父表）
        cursor.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        docs_deleted = cursor.rowcount

        conn.commit()

        return {
            "status": "success",
            "message": f"文档已删除 (documents: {docs_deleted}, chunks: {chunks_deleted})",
            "deleted": {
                "documents": docs_deleted,
                "chunks": chunks_deleted
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")

    finally:
        conn.close()


# ============ 对话记录 ============

@router.get("/conversations")
async def get_conversations(
    session_id: str = None,
    trace_id: str = None,
    limit: int = 50,
):
    """查询对话记录"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if session_id:
            # 按 session_id 查询
            cursor.execute("""
                SELECT session_id, trace_id, role, content, created_at, engine
                FROM conversations
                WHERE session_id = ?
                ORDER BY created_at ASC
            """, (session_id,))
        elif trace_id:
            # 按 trace_id 查询
            cursor.execute("""
                SELECT session_id, trace_id, role, content, created_at, engine
                FROM conversations
                WHERE trace_id = ?
                ORDER BY created_at ASC
            """, (trace_id,))
        else:
            # 查询最近的对话
            cursor.execute("""
                SELECT session_id, trace_id, role, content, created_at, engine
                FROM conversations
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))

        conversations = []
        for row in cursor.fetchall():
            conversations.append({
                "session_id": row[0],
                "trace_id": row[1],
                "role": row[2],
                "content": row[3],
                "created_at": row[4],
                "engine": row[5]
            })

        return {"conversations": conversations, "total": len(conversations)}

    finally:
        conn.close()


@router.get("/traces/{trace_id}")
async def get_trace_details(
    trace_id: str,
):
    """获取完整执行 trace"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 获取对话消息
        cursor.execute("""
            SELECT role, content, created_at
            FROM conversations
            WHERE trace_id = ?
            ORDER BY created_at ASC
        """, (trace_id,))

        messages = [{"role": row[0], "content": row[1], "created_at": row[2]}
                   for row in cursor.fetchall()]

        # 获取 Agent 执行记录
        cursor.execute("""
            SELECT agent_name, status, duration_ms, created_at, error
            FROM agent_executions
            WHERE trace_id = ?
            ORDER BY created_at ASC
        """, (trace_id,))

        executions = []
        for row in cursor.fetchall():
            executions.append({
                "agent_name": row[0],
                "status": row[1],
                "duration_ms": row[2],
                "created_at": row[3],
                "error": row[4]
            })

        return {
            "trace_id": trace_id,
            "messages": messages,
            "executions": executions
        }

    finally:
        conn.close()


@router.delete("/conversations/{session_id}")
async def delete_conversation(
    session_id: str,
):
    """删除指定会话的所有对话记录"""

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 删除对话记录
        cursor.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        deleted_count = cursor.rowcount

        if deleted_count == 0:
            raise HTTPException(status_code=404, detail="会话不存在")

        # 同时删除相关的 agent 执行记录（如果有）
        cursor.execute("""
            DELETE FROM agent_executions
            WHERE trace_id IN (
                SELECT DISTINCT trace_id
                FROM conversations
                WHERE session_id = ?
            )
        """, (session_id,))

        conn.commit()

        return {
            "status": "success",
            "message": f"已删除会话 {session_id} 的 {deleted_count} 条对话记录"
        }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")

    finally:
        conn.close()


# ============ 系统状态 ============

@router.get("/system/status")
async def get_system_status():
    """获取系统状态"""

    # 获取 LLM 配置（优先从数据库读取，回退到环境变量）
    config = ConfigManager.get_llm_config()
    llm_model = config.get("model") or os.getenv("LLM_MODEL", "未配置")
    llm_base_url = config.get("base_url") or os.getenv("LLM_BASE_URL", "未配置")
    llm_api_key = config.get("api_key") or os.getenv("LLM_API_KEY", "")
    llm_provider = config.get("provider", "未配置")
    llm_protocol = config.get("protocol", "openai")
    has_api_key = bool(llm_api_key and llm_api_key != "")

    return {
        "engine": {
            "name": "LangGraph"
        },
        "llm": {
            "provider": llm_provider,
            "protocol": llm_protocol,
            "model": llm_model,
            "base_url": llm_base_url,
            "has_api_key": has_api_key
        },
        "database": {
            "path": os.getenv("DB_PATH", "platform.db"),
            "exists": os.path.exists(os.getenv("DB_PATH", "platform.db"))
        }
    }


@router.get("/logs")
async def get_request_logs(
    limit: int = 50,
    authorization: str = Header(None)
):
    """获取最近的请求日志"""

    from middleware.request_logger import get_recent_logs
    logs = get_recent_logs(limit)
    return {"logs": logs[::-1]}  # 倒序，最新的在前


# ============ LLM 模型配置 ============

class LLMConfig(BaseModel):
    provider: str
    base_url: str
    api_key: str
    model: str
    protocol: str  # 新增协议字段


@router.get("/llm-config")
async def get_llm_config():
    """获取 LLM 配置（API Key 脱敏）"""
    config = ConfigManager.get_llm_config()

    # 脱敏 API Key
    if config["api_key"]:
        config["api_key_masked"] = ConfigManager.mask_api_key(config["api_key"])
        config["has_api_key"] = True
        del config["api_key"]  # 不返回明文
    else:
        config["api_key_masked"] = ""
        config["has_api_key"] = False

    return config


@router.post("/llm-config")
async def save_llm_config(
    config: LLMConfig,
):
    """保存 LLM 配置"""
    try:
        # 验证 Base URL 格式
        if config.base_url and not config.base_url.startswith("http"):
            raise HTTPException(status_code=400, detail="Base URL 必须以 http:// 或 https:// 开头")

        # 保存配置
        ConfigManager.save_llm_config(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            provider=config.provider,
            protocol=config.protocol
        )

        return {
            "status": "success",
            "message": "配置已保存，立即生效（无需重启）"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/models")
async def get_available_models(
    base_url: str,
    api_key: str,
    protocol: str = "openai",
):
    """获取可用模型列表（支持多协议）"""

    if not base_url or not api_key:
        raise HTTPException(status_code=400, detail="Base URL 和 API Key 不能为空")

    try:
        from core.llm_client import create_llm_client

        # 创建客户端（model 参数在获取列表时不重要，用空字符串）
        client = create_llm_client(protocol, base_url, api_key, "model-probe")
        models = await client.list_models()

        if not models:
            return {
                "status": "unsupported",
                "message": "该厂商不支持自动获取模型列表，请手动输入模型名",
                "models": [],
                "manual_input": True
            }

        return {
            "status": "success",
            "models": models,
            "manual_input": False,
            "count": len(models)
        }

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"请求失败: {e.response.text}"
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="请求超时，请检查 Base URL 是否正确")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}")


class TestConnectionRequest(BaseModel):
    base_url: str
    api_key: str
    model: str
    protocol: str = "openai"  # 新增协议字段


@router.post("/llm-config/test")
async def test_llm_connection(
    req: TestConnectionRequest,
):
    """测试 LLM 连接（支持多协议）"""

    if not req.base_url or not req.api_key or not req.model:
        raise HTTPException(status_code=400, detail="Base URL、API Key 和模型名不能为空")

    try:
        from core.llm_client import create_llm_client

        # 创建客户端
        client = create_llm_client(req.protocol, req.base_url, req.api_key, req.model)

        # 发送测试消息（询问模型身份，避免触发反探测）
        messages = [{"role": "user", "content": "你是什么模型？"}]
        reply = await client.chat(messages)

        return {
            "status": "success",
            "message": "连接成功，模型可用",
            "reply": reply,
            "model": req.model,
            "protocol": req.protocol
        }

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text
        try:
            error_json = e.response.json()
            if "error" in error_json:
                if isinstance(error_json["error"], dict):
                    error_detail = error_json["error"].get("message", error_detail)
                else:
                    error_detail = str(error_json["error"])
        except:
            pass

        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"连接失败: {error_detail}"
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="连接超时，请检查网络或 Base URL")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"测试失败: {str(e)}")


# ============ RAG 配置 ============

class RAGConfigUpdate(BaseModel):
    threshold: float


@router.get("/rag-config")
async def get_rag_config():
    """获取 RAG 配置"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT value FROM system_config WHERE key = ?", ("rag_threshold",))
        row = cursor.fetchone()
        threshold = float(row[0]) if row else 0.35

        cursor.execute("SELECT value FROM system_config WHERE key = ?", ("customer_service_channels",))
        row = cursor.fetchone()
        channels = json.loads(row[0]) if row else []

        return {
            "threshold": threshold,
            "customer_service_channels": channels
        }
    finally:
        conn.close()


@router.post("/rag-config")
async def update_rag_config(
    config: RAGConfigUpdate,
):
    """更新 RAG 阈值配置"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT OR REPLACE INTO system_config (key, value, updated_at)
            VALUES (?, ?, ?)
        """, ("rag_threshold", str(config.threshold), datetime.now().isoformat()))

        conn.commit()
        return {"status": "success", "message": f"RAG 阈值已更新为 {config.threshold}"}
    finally:
        conn.close()


class CustomerServiceChannel(BaseModel):
    name: str
    type: str
    value: str


class CSChannelsUpdate(BaseModel):
    channels: List[CustomerServiceChannel]


@router.post("/customer-service-channels")
async def update_cs_channels(
    config: CSChannelsUpdate,
):
    """更新客服渠道配置"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        channels_json = json.dumps([ch.dict() for ch in config.channels], ensure_ascii=False)

        cursor.execute("""
            INSERT OR REPLACE INTO system_config (key, value, updated_at)
            VALUES (?, ?, ?)
        """, ("customer_service_channels", channels_json, datetime.now().isoformat()))

        conn.commit()
        return {"status": "success", "message": f"客服渠道已更新，共 {len(config.channels)} 个"}
    finally:
        conn.close()


@router.get("/handoff-tickets")
async def get_handoff_tickets(
    status: str = None,
    limit: int = 50,
):
    """获取转人工工单列表"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if status:
            cursor.execute("""
                SELECT id, session_id, user_id, question, status, created_at
                FROM handoff_tickets
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (status, limit))
        else:
            cursor.execute("""
                SELECT id, session_id, user_id, question, status, created_at
                FROM handoff_tickets
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))

        tickets = []
        for row in cursor.fetchall():
            tickets.append({
                "id": row[0],
                "session_id": row[1],
                "user_id": row[2],
                "question": row[3],
                "status": row[4],
                "created_at": row[5]
            })

        return {"tickets": tickets, "total": len(tickets)}
    finally:
        conn.close()


# ============ 执行轨迹查询 ============

@router.get("/execution-traces")
async def get_execution_traces(
    session_id: str = None,
    limit: int = 50,
):
    """获取执行轨迹列表（按会话或全部）"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if session_id:
            cursor.execute("""
                SELECT id, session_id, trace_id, agent, engine, latency_ms, kb_hit, created_at
                FROM request_logs
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (session_id, limit))
        else:
            cursor.execute("""
                SELECT id, session_id, trace_id, agent, engine, latency_ms, kb_hit, created_at
                FROM request_logs
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))

        traces = []
        for row in cursor.fetchall():
            traces.append({
                "id": row[0],
                "session_id": row[1],
                "trace_id": row[2],
                "agent": row[3],
                "engine": row[4],
                "latency_ms": row[5],
                "kb_hit": row[6] == 1,
                "created_at": row[7]
            })

        return {"traces": traces, "total": len(traces)}
    finally:
        conn.close()


@router.get("/execution-traces/{trace_id}")
async def get_trace_detail(
    trace_id: str,
):
    """获取单个执行轨迹的详细步骤"""
    import json
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT session_id, trace_id, agent, engine, latency_ms, kb_hit, trace_data, created_at
            FROM request_logs
            WHERE trace_id = ?
        """, (trace_id,))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="执行轨迹不存在")

        trace_data = json.loads(row[6]) if row[6] else []

        # 获取相关的对话消息
        cursor.execute("""
            SELECT role, content, created_at
            FROM conversations
            WHERE trace_id = ?
            ORDER BY created_at ASC
        """, (trace_id,))

        messages = []
        for msg_row in cursor.fetchall():
            messages.append({
                "role": msg_row[0],
                "content": msg_row[1],
                "created_at": msg_row[2]
            })

        return {
            "session_id": row[0],
            "trace_id": row[1],
            "agent": row[2],
            "engine": row[3],
            "latency_ms": row[4],
            "kb_hit": row[5] == 1,
            "created_at": row[7],
            "steps": trace_data,
            "messages": messages
        }
    finally:
        conn.close()


@router.post("/test-send")
async def test_send_reply(request: dict):
    """
    临时测试接口：自动选会话 + 发送 test

    Body:
        {
            "target": "买家昵称",
            "content": "test"
        }
    """
    try:
        from platforms.goofish_playwright import GoofishPlaywrightPlatform

        target = request.get("target")
        content = request.get("content", "test")

        if not target:
            return {
                "status": "error",
                "detail": "Missing 'target' parameter"
            }

        platform = GoofishPlaywrightPlatform()

        # 调用 send_reply（使用 approval_id="test" 绕过审批）
        result = platform.send_reply(
            conversation_id="test_conversation",
            content=content,
            approval_id="test",
            target=target
        )

        return {
            "status": "ok",
            "send_result": result
        }

    except Exception as e:
        logger.exception("test_send_reply failed")
        return {
            "status": "error",
            "detail": str(e)
        }


@router.get("/poll-messages")
async def poll_messages():
    """
    轮询未读消息（影子模式：只读不发）

    遍历会话列表，找出有未读且需要回复的会话。
    【绝不发送消息，只读取+记录】

    Returns:
        {
            "status": "ready" | "need_login" | "failed",
            "detail": "描述",
            "conversations": [...],
            "total": 5,
            "needs_reply_count": 3,
            "screenshot": "失败截图路径（仅 failed 时）"
        }
    """
    try:
        from platforms.goofish_playwright import GoofishPlaywrightPlatform

        platform = GoofishPlaywrightPlatform()

        # 调用轮询（只读不发，返回三态结构）
        result = platform.poll_unread_conversations()

        # result 结构:
        # {
        #     "status": "ready" | "need_login" | "failed",
        #     "detail": "描述",
        #     "screenshot": "截图路径",
        #     "conversations": [...]
        # }

        status = result.get("status", "failed")
        conversations = result.get("conversations", [])

        if status == "ready":
            # 页面就绪，统计会话
            needs_reply_count = sum(1 for c in conversations if c.get("needs_reply"))

            return {
                "status": "ready",
                "detail": result.get("detail", ""),
                "conversations": conversations,
                "total": len(conversations),
                "needs_reply_count": needs_reply_count
            }

        elif status == "need_login":
            # 需要登录
            return {
                "status": "need_login",
                "detail": result.get("detail", "Login required"),
                "screenshot": result.get("screenshot"),
                "conversations": [],
                "total": 0,
                "needs_reply_count": 0
            }

        else:
            # 失败
            return {
                "status": "failed",
                "detail": result.get("detail", "Unknown error"),
                "screenshot": result.get("screenshot"),
                "conversations": [],
                "total": 0,
                "needs_reply_count": 0
            }

    except Exception as e:
        logger.exception("poll_messages failed")
        return {
            "status": "failed",
            "detail": f"Exception: {str(e)}",
            "conversations": [],
            "total": 0,
            "needs_reply_count": 0
        }
