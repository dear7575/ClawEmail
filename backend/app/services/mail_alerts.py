import html
import logging
import threading
from typing import Any
from urllib.parse import urlsplit

from app.db.mail_repository import MailRepository, mail_repository
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
    """邮件通知及失败补发服务。"""

    RETRY_POLL_SECONDS = 5
    INITIAL_RETRY_SECONDS = 30
    MAX_RETRY_SECONDS = 3600

    def __init__(
        self,
        telegram_service_value: TelegramService = telegram_service,
        settings_repository_value: SettingsRepository = settings_repository,
        mail_repository_value: MailRepository = mail_repository,
    ) -> None:
        self.telegram_service = telegram_service_value
        self.settings_repository = settings_repository_value
        self.mail_repository = mail_repository_value
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.lifecycle_lock = threading.Lock()
        self.send_lock = threading.Lock()

    def start(self) -> None:
        """启动持久化通知任务的后台重试线程。"""

        with self.lifecycle_lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, name="telegram-notification-retry", daemon=True)
            self.thread.start()
            logger.info("Telegram 通知补发调度器已启动")

    def shutdown(self) -> None:
        """停止通知补发线程。"""

        with self.lifecycle_lock:
            self.stop_event.set()
            thread = self.thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        logger.info("Telegram 通知补发调度器已停止")

    def _run(self) -> None:
        while not self.stop_event.wait(self.RETRY_POLL_SECONDS):
            try:
                self.retry_pending_once()
            except Exception as exc:
                logger.error("Telegram 通知补发轮询失败：error=%s", exc)

    def notify_new_mail(self, mail: dict[str, Any]) -> dict[str, Any]:
        """持久化新邮件通知并立即尝试发送。"""

        mail_id = mail.get("id")
        if mail_id is None:
            logger.error("新邮件 Telegram 通知未入队：缺少本地邮件 ID")
            return {"sent": False, "error": "missing mail id"}
        self.mail_repository.enqueue_telegram_notification(int(mail_id))
        with self.send_lock:
            return self._attempt_notification(mail, attempts=0)

    def retry_pending_once(self) -> None:
        """发送所有到期的持久化通知任务。"""

        if not self.send_lock.acquire(blocking=False):
            return
        try:
            for mail in self.mail_repository.list_due_telegram_notifications():
                self._attempt_notification(mail, attempts=int(mail.get("attempts") or 0))
        finally:
            self.send_lock.release()

    def _attempt_notification(self, mail: dict[str, Any], attempts: int) -> dict[str, Any]:
        mail_id = int(mail["id"])
        try:
            settings = self.telegram_service.get_settings()
            message, parse_mode, keyboard = format_new_mail_message(
                mail,
                settings.message_format,
                self.settings_repository.get(WEBHOOK_BASE_URL_KEY) or "",
            )
            self.telegram_service.send_message(message, parse_mode=parse_mode, reply_markup=keyboard)
        except Exception as exc:
            error_type = type(exc).__name__
            retry_after = min(self.MAX_RETRY_SECONDS, self.INITIAL_RETRY_SECONDS * (2 ** min(attempts, 7)))
            self.mail_repository.fail_telegram_notification(mail_id, error_type, retry_after)
            logger.error(
                "新邮件 Telegram 通知失败，将补发：mailId=%s mailbox=%s attempts=%s retryAfterSeconds=%s errorType=%s",
                mail_id,
                mail.get("mailbox_email"),
                attempts + 1,
                retry_after,
                error_type,
            )
            return {"sent": False, "error": str(exc)}

        self.mail_repository.complete_telegram_notification(mail_id)
        logger.info("新邮件 Telegram 通知已发送：mailId=%s mailbox=%s", mail_id, mail.get("mailbox_email"))
        return {"sent": True}


mail_alert_service = MailAlertService()
