"""①全站鉴权: Authorization: Bearer <token>, token == admin_password。

- admin_password 来源 config.settings.admin_password (alias ADMIN_PASSWORD, 从 env 读)。
- 用 secrets.compare_digest 常量时间比较, 防时序侧信道。
- 挂在 app.py 的 include_router(dependencies=[Depends(verify_token)]) 层,
  保护全部业务 router; 公开端点(/ /admin /static)是 app 级路由/挂载, 不受影响。
"""
import secrets

from fastapi import Header, HTTPException

from config import settings


def verify_token(authorization: str = Header(None)) -> None:
    expected = settings.admin_password
    if not (authorization and authorization.startswith("Bearer ")):
        raise HTTPException(status_code=401, detail="缺少 Authorization: Bearer token")
    token = authorization[len("Bearer "):].strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Token 无效")
