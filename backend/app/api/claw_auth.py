import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services.claw_auth import claw_auth_service

router = APIRouter(tags=["claw-auth"])
logger = logging.getLogger(__name__)


class SendCodeBody(BaseModel):
    """Claw 验证码发送请求。"""

    email: str


class VerifyCodeBody(BaseModel):
    """Claw 验证码校验请求。"""

    email: str
    code: str = Field(pattern=r"^\d+$")
    connectionId: str | None = None

    @field_validator("code")
    @classmethod
    def trim_code(cls, value: str) -> str:
        """去掉验证码首尾空白。"""

        return value.strip()


@router.get("/api/auth/claw/status")
def get_claw_auth_status() -> dict[str, Any]:
    """读取 legacy Claw 连接状态。"""

    return claw_auth_service.status()


@router.post("/api/auth/claw/send-code")
def send_claw_login_code(body: SendCodeBody) -> dict[str, bool]:
    """向 legacy Claw 账号发送登录验证码。"""

    logger.info("API 发送 legacy Claw 登录验证码：email=%s", body.email)
    claw_auth_service.send_code(body.email)
    return {"success": True}


@router.post("/api/auth/claw/verify-code")
def verify_claw_login_code(body: VerifyCodeBody) -> dict[str, Any]:
    """校验 legacy Claw 登录验证码并保存连接。"""

    logger.info("API 校验 legacy Claw 登录验证码：email=%s", body.email)
    return claw_auth_service.verify_code(body.email, body.code, "legacy")


@router.post("/api/auth/claw/refresh")
def refresh_claw_connection() -> dict[str, Any]:
    """刷新 legacy Claw 连接上下文。"""

    return claw_auth_service.refresh("legacy")


@router.post("/api/auth/claw/logout")
def logout_claw_connection() -> dict[str, Any]:
    """断开 legacy Claw 连接。"""

    return claw_auth_service.logout("legacy")


@router.get("/api/connections")
def list_connections() -> dict[str, list[dict[str, Any]]]:
    """列出所有 Claw 连接状态。"""

    return {"items": claw_auth_service.list_connections()}


@router.get("/api/connections/{connection_id}")
def get_connection(connection_id: str) -> dict[str, Any]:
    """读取指定 Claw 连接状态。"""

    connection = claw_auth_service.get_connection(connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="connection not found")
    return connection


@router.post("/api/connections/send-code")
def send_connection_login_code(body: SendCodeBody) -> dict[str, bool]:
    """向多连接模式的 Claw 账号发送登录验证码。"""

    logger.info("API 发送 Claw 连接登录验证码：email=%s", body.email)
    claw_auth_service.send_code(body.email)
    return {"success": True}


@router.post("/api/connections/verify-code")
def verify_connection_login_code(body: VerifyCodeBody) -> dict[str, Any]:
    """校验多连接模式的 Claw 登录验证码。"""

    logger.info("API 校验 Claw 连接登录验证码：email=%s connection=%s", body.email, body.connectionId or "auto")
    return claw_auth_service.verify_code(body.email, body.code, body.connectionId)


@router.post("/api/connections/{connection_id}/refresh")
def refresh_connection(connection_id: str) -> dict[str, Any]:
    """刷新指定 Claw 连接上下文。"""

    return claw_auth_service.refresh(connection_id)


@router.post("/api/connections/{connection_id}/logout")
def logout_connection(connection_id: str) -> dict[str, Any]:
    """断开指定 Claw 连接。"""

    return claw_auth_service.logout(connection_id)


@router.post("/api/connections/{connection_id}/delete")
def delete_connection_with_post(connection_id: str) -> dict[str, bool]:
    """删除指定 Claw 连接的本地记录，兼容不支持 DELETE 的部署层。"""

    return delete_connection(connection_id)


@router.delete("/api/connections/{connection_id}")
def delete_connection(connection_id: str) -> dict[str, bool]:
    """删除指定 Claw 连接的本地记录。"""

    logger.info("API 删除 Claw 连接：connection=%s", connection_id)
    if not claw_auth_service.delete_connection(connection_id):
        raise HTTPException(status_code=404, detail="connection not found")
    return {"success": True}
