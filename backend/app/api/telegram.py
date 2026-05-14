import logging

from fastapi import APIRouter

from app.services.telegram import (
    TelegramMessage,
    TelegramPublicSettings,
    TelegramSettingsUpdate,
    telegram_service,
)

router = APIRouter(tags=["telegram"])
logger = logging.getLogger(__name__)


@router.get("/api/telegram/settings", response_model=TelegramPublicSettings, response_model_by_alias=True)
def get_telegram_settings() -> TelegramPublicSettings:
    """读取 Telegram 公开配置。"""

    return telegram_service.to_public_settings()


@router.put("/api/telegram/settings", response_model=TelegramPublicSettings, response_model_by_alias=True)
def update_telegram_settings(body: TelegramSettingsUpdate) -> TelegramPublicSettings:
    """更新 Telegram 配置。"""

    logger.info("API 更新 Telegram 配置：enabled=%s hasBotToken=%s hasChatId=%s", body.enabled, body.bot_token is not None, body.chat_id is not None)
    return telegram_service.save_settings(body)


@router.post("/api/telegram/send")
def send_telegram_message(body: TelegramMessage) -> dict[str, bool]:
    """发送 Telegram 测试消息。"""

    logger.info("API 发送 Telegram 消息：length=%s", len(body.text))
    telegram_service.send_message(body.text)
    return {"success": True}
