import logging
from typing import Any

from fastapi import APIRouter

from app.services.openai_push import OpenAiDuckPushBody, openai_push_service

router = APIRouter(tags=["openai"])
logger = logging.getLogger(__name__)


@router.post("/api/openai/duck-push-sub2")
def push_openai_duck_address_to_sub2(body: OpenAiDuckPushBody) -> dict[str, Any]:
    """将已保存 OpenAI OAuth JSON 的 Duck 地址推送到 Sub2API。"""

    logger.info("API 推送 OpenAI Duck 地址到 Sub2：duckAddressId=%s groupId=%s", body.duck_address_id, body.group_id)
    return {
        "success": True,
        **openai_push_service.push_duck_address_to_sub2(body.duck_address_id, body.group_id),
    }
