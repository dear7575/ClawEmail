import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.services.openai_push import OpenAiDuckPushBody, openai_push_job_service

router = APIRouter(tags=["openai"])
logger = logging.getLogger(__name__)


@router.post("/api/openai/duck-push-sub2")
def push_openai_duck_address_to_sub2(body: OpenAiDuckPushBody) -> dict[str, Any]:
    """启动 Duck 地址推送到 Sub2API 的后台任务。"""

    logger.info(
        "API 推送 OpenAI Duck 地址到 Sub2：duckAddressId=%s groupId=%s proxyId=%s",
        body.duck_address_id,
        body.group_id,
        body.proxy_id,
    )
    return {
        "success": True,
        **openai_push_job_service.start(body.duck_address_id, body.group_id, body.proxy_id),
    }


@router.get("/api/openai/duck-push-sub2/jobs/{job_id}")
def get_openai_duck_address_push_job(job_id: str) -> dict[str, Any]:
    """查询 Duck 地址推送到 Sub2API 的后台任务状态。"""

    job = openai_push_job_service.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="OpenAI Duck 推送任务不存在或已过期")
    return {"success": True, **job}
