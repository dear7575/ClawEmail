import asyncio
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.services.telegram import (
    TelegramMessage,
    TelegramPublicSettings,
    TelegramSettingsUpdate,
    WEBHOOK_SECRET_KEY,
    telegram_service,
)
from app.db.settings_repository import settings_repository
from app.services.telegram_bot import telegram_bot_service

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


@router.post("/api/telegram/commands/install")
def install_telegram_commands() -> dict[str, bool]:
    """注册 Telegram 命令并安装 Webhook。"""

    return telegram_bot_service.install()


@router.delete("/api/telegram/commands")
def uninstall_telegram_commands() -> dict[str, bool]:
    """移除 Telegram 命令及 Webhook。"""

    return telegram_bot_service.uninstall()


@router.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict[str, bool]:
    """验证 Telegram Webhook 密钥并处理 Bot 命令。"""

    expected_header = settings_repository.get(WEBHOOK_SECRET_KEY)
    if not expected_header or not secret_token or not hmac.compare_digest(secret_token, expected_header):
        raise HTTPException(status_code=403, detail="invalid webhook secret")
    update = await request.json()
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="invalid update")
    await asyncio.to_thread(telegram_bot_service.handle_update, update)
    return {"ok": True}


@router.post("/api/telegram/send")
def send_telegram_message(body: TelegramMessage) -> dict[str, bool]:
    """发送 Telegram 测试消息。"""

    logger.info("API 发送 Telegram 消息：length=%s", len(body.text))
    telegram_service.send_message(body.text)
    return {"success": True}
