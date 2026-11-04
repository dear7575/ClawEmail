import logging
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_settings
from app.db.settings_repository import SettingsRepository, settings_repository
from app.services.network_settings import network_settings_service


logger = logging.getLogger(__name__)
ENABLED_KEY = "telegram.enabled"
BOT_TOKEN_KEY = "telegram.botToken"
CHAT_ID_KEY = "telegram.chatId"
FORMAT_KEY = "telegram.format"
WEBHOOK_SECRET_KEY = "telegram.webhookSecret"
WEBHOOK_URL_KEY = "telegram.webhookUrl"
WEBHOOK_BASE_URL_KEY = "telegram.webhookBaseUrl"
LAST_UPDATE_KEY = "telegram.lastUpdateId"
TELEGRAM_API_ORIGIN = "https://api.telegram.org"
TELEGRAM_COMMANDS = [
    {"command": "start", "description": "查看邮件助手状态"},
    {"command": "help", "description": "查看可用命令"},
    {"command": "latest", "description": "查看最新邮件"},
    {"command": "unread", "description": "查看未读邮件"},
]


class TelegramSettings(BaseModel):
    """Telegram 私有配置，包含 Bot Token。"""

    enabled: bool = False
    chat_id: str = Field(default="", alias="chatId")
    bot_token: str = Field(default="", alias="botToken")
    message_format: str = Field(default="html", alias="messageFormat")
    webhook_base_url: str = Field(default="", alias="webhookBaseUrl")

    model_config = ConfigDict(populate_by_name=True)


class TelegramPublicSettings(BaseModel):
    """返回给前端的 Telegram 配置，Token 只暴露脱敏预览。"""

    enabled: bool = False
    chat_id: str = Field(default="", alias="chatId")
    has_bot_token: bool = Field(default=False, alias="hasBotToken")
    bot_token_preview: str | None = Field(default=None, alias="botTokenPreview")
    webhook_base_url: str = Field(default="", alias="webhookBaseUrl")
    webhook_base_url_configured: bool = Field(default=False, alias="webhookBaseUrlConfigured")
    commands_installed: bool = Field(default=False, alias="commandsInstalled")
    message_format: str = Field(default="html", alias="messageFormat")

    model_config = ConfigDict(populate_by_name=True)


class TelegramSettingsUpdate(BaseModel):
    """Telegram 配置更新请求。"""

    enabled: bool | None = None
    bot_token: str | None = Field(default=None, alias="botToken", max_length=200)
    chat_id: str | None = Field(default=None, alias="chatId", max_length=120)
    webhook_base_url: str | None = Field(default=None, alias="webhookBaseUrl", max_length=500)
    message_format: str | None = Field(default=None, alias="messageFormat", pattern="^(html|plain)$")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("bot_token", "chat_id", "webhook_base_url")
    @classmethod
    def trim_optional_string(cls, value: str | None) -> str | None:
        """去掉可选字符串配置首尾空白。"""

        return value.strip() if value is not None else None


class TelegramMessage(BaseModel):
    """Telegram 文本消息请求。"""

    text: str = Field(min_length=1, max_length=4096)

    @field_validator("text")
    @classmethod
    def trim_text(cls, value: str) -> str:
        """去掉消息文本首尾空白。"""

        return value.strip()


def normalize_string(value: str | None) -> str:
    """归一化可空字符串配置。"""

    return (value or "").strip()


def mask_bot_token(token: str) -> str | None:
    """生成 Telegram Bot Token 脱敏预览。"""

    if not token:
        return None
    if len(token) <= 12:
        return f"{token[:4]}****"
    return f"{token[:8]}...{token[-4:]}"


class TelegramService:
    """迁移版 Telegram 配置与发送服务。"""

    def __init__(
        self,
        repository: SettingsRepository = settings_repository,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        """初始化 Telegram 服务。"""

        self.repository = repository
        self.client_factory = client_factory

    def get_settings(self) -> TelegramSettings:
        """读取 Telegram 配置，兼容环境变量和 SQLite 持久化配置。"""

        settings = get_settings()
        return TelegramSettings(
            enabled=(self.repository.get(ENABLED_KEY) or "false") == "true",
            botToken=normalize_string(self.repository.get(BOT_TOKEN_KEY) or settings.telegram_bot_token),
            chatId=normalize_string(self.repository.get(CHAT_ID_KEY) or settings.telegram_chat_id),
            messageFormat=self.repository.get(FORMAT_KEY) or "html",
            webhookBaseUrl=normalize_string(self.repository.get(WEBHOOK_BASE_URL_KEY)),
        )

    def to_public_settings(self, settings: TelegramSettings | None = None) -> TelegramPublicSettings:
        """转换为前端可展示的 Telegram 配置。"""

        current = settings or self.get_settings()
        return TelegramPublicSettings(
            enabled=current.enabled,
            chatId=current.chat_id,
            hasBotToken=bool(current.bot_token),
            botTokenPreview=mask_bot_token(current.bot_token),
            messageFormat=current.message_format,
            webhookBaseUrl=current.webhook_base_url,
            commandsInstalled=bool(self.repository.get(WEBHOOK_SECRET_KEY)),
            webhookBaseUrlConfigured=bool(current.webhook_base_url.strip()),
        )

    def save_settings(self, update: TelegramSettingsUpdate) -> TelegramPublicSettings:
        """保存 Telegram 配置。

        参数:
            update: 配置更新请求；未传字段保持现值。

        返回:
            脱敏后的公开配置。
        """

        current = self.get_settings()
        if self.repository.get(WEBHOOK_SECRET_KEY):
            changes_bot = update.bot_token is not None and normalize_string(update.bot_token) != current.bot_token
            changes_chat = update.chat_id is not None and normalize_string(update.chat_id) != current.chat_id
            changes_webhook = update.webhook_base_url is not None and normalize_string(update.webhook_base_url) != current.webhook_base_url
            disables_bot = update.enabled is False
            if changes_bot or changes_chat or changes_webhook or disables_bot:
                raise ValueError("请先卸载 Telegram 邮件命令，再修改 Bot Token、Chat ID 或停用通知")
        next_settings = TelegramSettings(
            enabled=current.enabled if update.enabled is None else update.enabled,
            botToken=current.bot_token if update.bot_token is None else update.bot_token,
            chatId=current.chat_id if update.chat_id is None else update.chat_id,
            messageFormat=current.message_format if update.message_format is None else update.message_format,
            webhookBaseUrl=current.webhook_base_url if update.webhook_base_url is None else update.webhook_base_url,
        )
        self.repository.set(ENABLED_KEY, str(next_settings.enabled).lower())
        self.repository.set(BOT_TOKEN_KEY, next_settings.bot_token)
        self.repository.set(CHAT_ID_KEY, next_settings.chat_id)
        self.repository.set(FORMAT_KEY, next_settings.message_format)
        self.repository.set(WEBHOOK_BASE_URL_KEY, next_settings.webhook_base_url)
        logger.info(
            "保存 Telegram 配置：enabled=%s hasBotToken=%s hasChatId=%s",
            next_settings.enabled,
            bool(next_settings.bot_token),
            bool(next_settings.chat_id),
        )
        return self.to_public_settings(next_settings)

    def call_api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """调用 Telegram Bot API 并校验返回状态。"""

        settings = self.get_settings()
        if not settings.bot_token:
            raise ValueError("请先配置 Telegram Bot Token")
        network_settings = network_settings_service.get()
        with self.client_factory(
            timeout=network_settings.timeout_ms / 1000,
            proxy=network_settings.proxy_url or None,
        ) as client:
            response = client.request(
                "POST",
                f"{TELEGRAM_API_ORIGIN}/bot{settings.bot_token}/{method}",
                headers={"content-type": "application/json"},
                json=payload,
            )
        try:
            body = response.json() if response.text.strip() else None
        except ValueError:
            body = None
        if not response.is_success or not isinstance(body, dict) or body.get("ok") is not True:
            description = body.get("description") if isinstance(body, dict) else response.reason_phrase
            raise RuntimeError(f"Telegram API {method} 失败：{description}")
        return body

    def send_message(self, text: str, parse_mode: str | None = None, reply_markup: dict[str, Any] | None = None) -> None:
        """发送 Telegram 文本消息。

        参数:
            text: 已校验的消息文本。
            parse_mode: 可选的 Telegram 消息解析模式。
            reply_markup: 可选的 Telegram 按钮布局。

        异常:
            ValueError: Telegram 未启用或缺少必要配置。
            RuntimeError: Telegram API 返回失败状态。
        """

        settings = self.get_settings()
        if not settings.enabled:
            logger.warning("Telegram 发送被拒绝：通知未启用")
            raise ValueError("Telegram 消息通知未启用")
        if not settings.bot_token or not settings.chat_id:
            logger.warning("Telegram 发送被拒绝：缺少 Bot Token 或 Chat ID")
            raise ValueError("请先在系统设置里配置 Telegram Bot Token 和 Chat ID")

        network_settings = network_settings_service.get()
        timeout_seconds = network_settings.timeout_ms / 1000
        proxy = network_settings.proxy_url or None
        logger.info(
            "开始发送 Telegram 消息：chatId=%s length=%s proxy=%s",
            settings.chat_id,
            len(text),
            bool(proxy),
        )
        with self.client_factory(timeout=timeout_seconds, proxy=proxy) as client:
            response = client.request(
                "POST",
                f"{TELEGRAM_API_ORIGIN}/bot{settings.bot_token}/sendMessage",
                headers={"content-type": "application/json"},
                json={
                    "chat_id": settings.chat_id,
                    "text": text,
                    **({"parse_mode": parse_mode} if parse_mode else {}),
                    **({"reply_markup": reply_markup} if reply_markup else {}),
                },
            )

        body: Any = None
        try:
            body = response.json() if response.text.strip() else None
        except ValueError:
            body = None
        if not response.is_success:
            description = body.get("description") if isinstance(body, dict) else response.reason_phrase
            logger.error("Telegram 发送失败：status=%s description=%s", response.status_code, description)
            raise RuntimeError(f"Telegram 发送失败：{description}")
        logger.info("Telegram 消息发送成功：chatId=%s status=%s", settings.chat_id, response.status_code)


telegram_service = TelegramService()
