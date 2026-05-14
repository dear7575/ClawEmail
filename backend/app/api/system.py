import logging

from fastapi import APIRouter

from app.services.network_settings import (
    NetworkSettings,
    NetworkSettingsUpdate,
    network_settings_service,
)

router = APIRouter(tags=["system"])
logger = logging.getLogger(__name__)


@router.get("/api/system/network-settings", response_model=NetworkSettings, response_model_by_alias=True)
def get_system_network_settings() -> NetworkSettings:
    """读取系统网络设置。"""

    return network_settings_service.get()


@router.put("/api/system/network-settings", response_model=NetworkSettings, response_model_by_alias=True)
def update_system_network_settings(body: NetworkSettingsUpdate) -> NetworkSettings:
    """更新系统网络设置。"""

    logger.info("API 更新系统网络设置：hasProxy=%s timeoutMs=%s otpTimeoutMs=%s", bool(body.proxy_url), body.timeout_ms, body.open_ai_otp_timeout_ms)
    return network_settings_service.save(body)


@router.get("/api/duck/network-settings", response_model=NetworkSettings, response_model_by_alias=True)
def get_duck_network_settings() -> NetworkSettings:
    """读取兼容旧前端路径的 Duck 网络设置。"""

    return network_settings_service.get()


@router.put("/api/duck/network-settings", response_model=NetworkSettings, response_model_by_alias=True)
def update_duck_network_settings(body: NetworkSettingsUpdate) -> NetworkSettings:
    """更新兼容旧前端路径的 Duck 网络设置。"""

    logger.info("API 更新 Duck 网络设置兼容路径：hasProxy=%s timeoutMs=%s otpTimeoutMs=%s", bool(body.proxy_url), body.timeout_ms, body.open_ai_otp_timeout_ms)
    return network_settings_service.save(body)
