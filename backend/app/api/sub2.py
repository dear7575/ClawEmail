import logging

from fastapi import APIRouter

from app.services.sub2 import (
    Sub2AccountPayload,
    Sub2ConvertResponse,
    Sub2GroupsResponse,
    Sub2PushResponse,
    Sub2PublicSettings,
    Sub2SettingsUpdate,
    sub2_service,
)

router = APIRouter(tags=["sub2"])
logger = logging.getLogger(__name__)


@router.get("/api/sub2/settings", response_model=Sub2PublicSettings, response_model_by_alias=True)
def get_sub2_settings() -> Sub2PublicSettings:
    """读取 Sub2API 公开配置。"""

    return sub2_service.to_public_settings()


@router.put("/api/sub2/settings", response_model=Sub2PublicSettings, response_model_by_alias=True)
def update_sub2_settings(body: Sub2SettingsUpdate) -> Sub2PublicSettings:
    """更新 Sub2API 配置。"""

    logger.info("API 更新 Sub2 配置：hasApiUrl=%s hasApiKey=%s groupId=%s", body.api_url is not None, body.api_key is not None, body.default_group_id)
    return sub2_service.save_settings(body)


@router.get("/api/sub2/groups", response_model=Sub2GroupsResponse)
def get_sub2_groups() -> Sub2GroupsResponse:
    """获取 Sub2 OpenAI 可用分组。"""

    logger.info("API 获取 Sub2 分组")
    return Sub2GroupsResponse(items=[
        group.model_dump() if hasattr(group, "model_dump") else group
        for group in sub2_service.fetch_groups()
    ])


@router.post("/api/sub2/convert", response_model=Sub2ConvertResponse)
def convert_sub2_account(body: Sub2AccountPayload) -> Sub2ConvertResponse:
    """转换 ChatGPT session JSON 为 Sub2 导入数据。"""

    return Sub2ConvertResponse(data=sub2_service.convert_account(body.input))


@router.post("/api/sub2/push", response_model=Sub2PushResponse)
def push_sub2_account(body: Sub2AccountPayload) -> Sub2PushResponse:
    """转换并推送 ChatGPT session JSON 到 Sub2API。"""

    logger.info("API 推送 Sub2 账号：groupId=%s", body.group_id)
    result = sub2_service.push_account(body.input, body.group_id)
    return Sub2PushResponse(success=True, **result)
