import html
import logging
import secrets
import threading
from typing import Any
from urllib.parse import urlsplit

from app.db.mail_repository import MailRepository, mail_repository
from app.db.settings_repository import SettingsRepository, settings_repository
from app.services.telegram import (
    LAST_UPDATE_KEY,
    TELEGRAM_COMMANDS,
    WEBHOOK_SECRET_KEY,
    WEBHOOK_URL_KEY,
    TelegramService,
    telegram_service,
)


logger = logging.getLogger(__name__)


class TelegramBotService:
    """安装 Telegram 命令并安全处理邮件查询更新。"""

    def __init__(
        self,
        repository: SettingsRepository = settings_repository,
        mail_repository_value: MailRepository = mail_repository,
        telegram_service_value: TelegramService = telegram_service,
    ) -> None:
        self.repository = repository
        self.mail_repository = mail_repository_value
        self.telegram_service = telegram_service_value
        self._update_lock = threading.Lock()

    def install(self) -> dict[str, bool]:
        telegram = self.telegram_service.get_settings()
        base_url = telegram.webhook_base_url.strip().rstrip("/")
        if not telegram.enabled or not telegram.bot_token or not telegram.chat_id:
            raise ValueError("请先启用 Telegram 并配置 Bot Token 和 Chat ID")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("请在系统设置中填写公开可访问的 HTTPS 地址")

        existing = self.telegram_service.call_api("getWebhookInfo", {})
        current_url = ((existing.get("result") or {}).get("url") or "").strip()
        webhook_url = f"{base_url}/api/telegram/webhook"
        current_secret = self.repository.get(WEBHOOK_SECRET_KEY)
        installed_url = self.repository.get(WEBHOOK_URL_KEY)
        if current_url and (current_url != webhook_url or current_url != installed_url or not current_secret):
            raise ValueError("此 Bot Token 已配置其他 Webhook，请先确认后再安装")

        header_secret = secrets.token_urlsafe(32)
        self.telegram_service.call_api("setMyCommands", {"commands": TELEGRAM_COMMANDS})
        self.telegram_service.call_api("setWebhook", {
            "url": webhook_url,
            "secret_token": header_secret,
            "allowed_updates": ["message"],
            "drop_pending_updates": False,
        })
        self.repository.set(WEBHOOK_SECRET_KEY, header_secret)
        self.repository.set(WEBHOOK_URL_KEY, webhook_url)
        self.repository.delete_many([LAST_UPDATE_KEY])
        logger.info("Telegram 邮件命令已安装：webhookPathConfigured=true")
        return {"installed": True}

    def uninstall(self) -> dict[str, bool]:
        current_secret = self.repository.get(WEBHOOK_SECRET_KEY)
        if current_secret:
            installed_url = self.repository.get(WEBHOOK_URL_KEY)
            existing = self.telegram_service.call_api("getWebhookInfo", {})
            current_url = ((existing.get("result") or {}).get("url") or "").strip()
            if current_url and current_url != installed_url:
                raise ValueError("Bot Webhook 已被其他服务更改，未执行卸载")
            if current_url:
                self.telegram_service.call_api("deleteWebhook", {"drop_pending_updates": False})
            self.telegram_service.call_api("setMyCommands", {"commands": []})
        self.repository.delete_many([WEBHOOK_SECRET_KEY, WEBHOOK_URL_KEY, LAST_UPDATE_KEY])
        logger.info("Telegram 邮件命令已卸载")
        return {"installed": False}

    def handle_update(self, update: dict[str, Any]) -> None:
        """串行处理 Telegram 更新以避免重复响应。"""

        with self._update_lock:
            self._handle_update(update)

    def _handle_update(self, update: dict[str, Any]) -> None:
        update_id = update.get("update_id")
        try:
            update_id_value = int(update_id)
        except (TypeError, ValueError):
            return
        previous = self.repository.get(LAST_UPDATE_KEY)
        if previous is not None and update_id_value <= int(previous):
            logger.info("跳过重复 Telegram 更新：updateId=%s", update_id_value)
            return

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        configured_chat_id = self.telegram_service.get_settings().chat_id
        if str(chat.get("id", "")) != configured_chat_id:
            logger.warning("忽略未授权 Telegram Chat 更新：updateId=%s", update_id_value)
            self.repository.set(LAST_UPDATE_KEY, str(update_id_value))
            return

        command_text = str(message.get("text") or "").strip().split()
        command = command_text[0].split("@", 1)[0].lower() if command_text else ""
        if not command.startswith("/"):
            self.repository.set(LAST_UPDATE_KEY, str(update_id_value))
            return
        if command in {"/start", "/help"}:
            response = "可用命令：\n/latest - 最近邮件\n/unread - 未读邮件"
        elif command in {"/latest", "/unread"}:
            mails = self.mail_repository.list_recent_mail_summaries(
                limit=5,
                unread_only=command == "/unread",
            )
            response = self._format_mail_list(mails, unread_only=command == "/unread")
        else:
            response = "未知命令。发送 /help 查看可用命令。"

        self.telegram_service.send_message(response, parse_mode="HTML")
        self.repository.set(LAST_UPDATE_KEY, str(update_id_value))

    @staticmethod
    def _format_mail_list(mails: list[dict[str, Any]], unread_only: bool) -> str:
        title = "未读邮件" if unread_only else "最近邮件"
        if not mails:
            return f"<b>{title}</b>\n暂无邮件。"
        lines = [f"<b>{title}</b>"]
        for mail in mails:
            subject = html.escape(str(mail.get("subject") or "(无主题)")[:180])
            sender = html.escape(str(mail.get("source") or "未知发件人")[:120])
            mailbox = html.escape(str(mail.get("mailbox_email") or "")[:120])
            received = html.escape(str(mail.get("received_at") or mail.get("created_at") or "")[:64])
            marker = "[附件] " if mail.get("has_attachments") else ""
            lines.append(f"\n\n{marker}<b>{subject}</b>\n{sender} → {mailbox}\n{received}")
        return "".join(lines)


telegram_bot_service = TelegramBotService()
