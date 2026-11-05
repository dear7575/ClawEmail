import html
import logging
from typing import Any
from urllib.parse import urlsplit

from app.db.settings_repository import SettingsRepository, settings_repository
from app.services.telegram import (
    WEBHOOK_BASE_URL_KEY,
    TelegramService,
    telegram_service,
)


logger = logging.getLogger(__name__)


def compact_text(value: Any, limit: int = 500) -> str:
    """将可选字段转换为适合 Telegram 展示的单行文本。"""

    text = str(value or "").strip()
    if not text:
        return "-"
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


def format_new_mail_message(
    mail: dict[str, Any],
    message_format: str = "html",
    webhook_base_url: str = "",
) -> tuple[str, str | None, dict[str, Any] | None]:
    """生成带安全 HTML 格式和收件箱按钮的新邮件通知。"""

    values = {
        "mailbox": compact_text(mail.get("address") or mail.get("mailbox_email"), 200),
        "source": compact_text(mail.get("source"), 300),
        "subject": compact_text(mail.get("subject"), 500),
        "received_at": compact_text(mail.get("received_at") or mail.get("created_at"), 120),
        "attachments": "有" if mail.get("has_attachments") else "无",
    }
    base_url = webhook_base_url.strip().rstrip("/")
    parsed_base_url = urlsplit(base_url)
    keyboard = None
    if parsed_base_url.scheme == "https" and parsed_base_url.netloc and not parsed_base_url.query and not parsed_base_url.fragment:
        keyboard = {"inline_keyboard": [[{"text": "打开 ClawEmail", "url": base_url}]]}

    if message_format == "plain":
        text = "\n".join([
            "收到新邮件",
            "",
            f"收件邮箱：{values['mailbox']}",
            f"发件人：{values['source']}",
            f"主题：{values['subject']}",
            f"时间：{values['received_at']}",
            f"附件：{values['attachments']}",
        ])
        return text, None, keyboard

    text = "\n".join([
        "<b>收到新邮件</b>",
        "",
        f"<b>收件邮箱：</b>{html.escape(values['mailbox'])}",
        f"<b>发件人：</b>{html.escape(values['source'])}",
        f"<b>主题：</b>{html.escape(values['subject'])}",
        f"<b>时间：</b>{html.escape(values['received_at'])}",
        f"<b>附件：</b>{values['attachments']}",
    ])
    return text, "HTML", keyboard


class MailAlertService:
    """邮件通知服务。"""

    def __init__(
        self,
        telegram_service_value: TelegramService = telegram_service,
        settings_repository_value: SettingsRepository = settings_repository,
    ) -> None:
        """初始化邮件通知服务。"""

        self.telegram_service = telegram_service_value
        self.settings_repository = settings_repository_value

    def notify_new_mail(self, mail: dict[str, Any]) -> dict[str, Any]:
        """收到新邮件时发送 Telegram 通知。"""

        mail_id = mail.get("id")
        try:
            settings = self.telegram_service.get_settings()
            message, parse_mode, keyboard = format_new_mail_message(
                mail,
                settings.message_format,
                self.settings_repository.get(WEBHOOK_BASE_URL_KEY) or "",
            )
            self.telegram_service.send_message(message, parse_mode=parse_mode, reply_markup=keyboard)
        except Exception as exc:
            logger.warning(
                "新邮件 Telegram 通知失败：mailId=%s mailbox=%s error=%s",
                mail_id,
                mail.get("mailbox_email"),
                exc,
            )
            return {"sent": False, "error": str(exc)}

        logger.info("新邮件 Telegram 通知已发送：mailId=%s mailbox=%s", mail_id, mail.get("mailbox_email"))
        return {"sent": True}


mail_alert_service = MailAlertService()
