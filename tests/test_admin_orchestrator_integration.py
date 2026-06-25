"""
R-02 行为测试:/auto-reply 端点必须走 orchestrator.run_once,不能再回到 shadow_pipeline。

行为契约:
1. POST /api/admin/auto-reply 调用一次 orchestrator.run_once()
2. shadow_pipeline.run_auto_reply 不被调用
3. shadow_pipeline 即便被 import 也不能执行真发逻辑(已 raise)
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.admin import router as admin_router


@pytest.fixture
def client():
    # admin_router 自带 prefix="/api/admin",include_router 不要再加前缀
    app = FastAPI()
    app.include_router(admin_router)
    return TestClient(app)


def test_auto_reply_endpoint_calls_orchestrator_run_once(client):
    """POST /api/admin/auto-reply 必须调用 orchestrator.run_once"""
    fake_return = {"scanned": 1, "processed": 1, "errors": []}
    with patch(
        "core.auto_reply_orchestrator.run_once", return_value=fake_return
    ) as p_run_once, patch(
        "core.shadow_pipeline.run_auto_reply"
    ) as p_shadow:
        resp = client.post("/api/admin/auto-reply")

    assert resp.status_code == 200
    assert p_run_once.call_count == 1, "/auto-reply 必须调用 orchestrator.run_once"
    assert p_shadow.call_count == 0, "/auto-reply 不能再调 shadow_pipeline.run_auto_reply"
    assert resp.json() == fake_return


def test_shadow_pipeline_run_auto_reply_raises():
    """shadow_pipeline.run_auto_reply 调用时必须 raise(不再发真消息)"""
    from core.shadow_pipeline import run_auto_reply
    with pytest.raises(RuntimeError, match="已废弃"):
        run_auto_reply()


def test_shadow_pipeline_module_imports_safely():
    """模块顶层 import 不应抛错(只在调用时 raise)"""
    import importlib
    import core.shadow_pipeline as sp
    importlib.reload(sp)
    # 模块顶层符号还在,但调用 run_auto_reply 必 raise
    assert hasattr(sp, "run_auto_reply")
    assert hasattr(sp, "DEPRECATED")
    assert sp.DEPRECATED is True
