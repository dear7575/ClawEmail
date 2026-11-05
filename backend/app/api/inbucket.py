import logging

from fastapi import APIRouter

from app.services.inbucket import (
    InbucketSettings,
    InbucketSettingsUpdate,
    inbucket_settings_service,
)

router = APIRouter(tags=["inbucket"])
logger = logging.getLogger(__name__)


@router.get("/api/inbucket/settings", response_model=InbucketSettings, response_model_by_alias=True)
def get_inbucket_settings() -> InbucketSettings:
    return inbucket_settings_service.get()


@router.put("/api/inbucket/settings", response_model=InbucketSettings, response_model_by_alias=True)
def update_inbucket_settings(body: InbucketSettingsUpdate) -> InbucketSettings:
    settings = inbucket_settings_service.save(body)
    from app.main import inbucket_monitor
    inbucket_monitor.reload_settings()
    logger.info("Inbucket 设置已保存：configured=%s hasCredentials=%s", bool(settings.base_url), settings.has_password)
    return settings
