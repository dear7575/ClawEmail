import logging
from typing import Any

from app.services.telegram import TelegramService, telegram_service


logger = logging.getLogger(__name__)


def compact_text(value: Any, limit: int = 500) -> str:
    """将可选字段转换为适合 Telegram 展示的单行文本。"""

    text = str(value or "").strip()
    if not text:
        return "-"
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


def format_new_mail_message(mail: dict[str, Any]) -> str:
    """生成新邮件 Telegram 通知文本。"""

    return "\n".join([
        "收到新邮件",
        "",
        f"收件邮箱：{compact_text(mail.get('mailbox_email'), 200)}",
        f"发件人：{compact_text(mail.get('source'), 300)}",
        f"主题：{compact_text(mail.get('subject'), 500)}",
        f"时间：{compact_text(mail.get('received_at') or mail.get('created_at'), 120)}",
        f"附件：{'有' if mail.get('has_attachments') else '无'}",
    ])


class MailAlertService:
    """邮件通知服务。"""

    def __init__(self, telegram_service_value: TelegramService = telegram_service) -> None:
        """初始化邮件通知服务。"""

        self.telegram_service = telegram_service_value

    def notify_new_mail(self, mail: dict[str, Any]) -> dict[str, Any]:
        """收到新邮件时发送 Telegram 通知。

        参数:
            mail: 已保存到本地数据库的邮件记录。

        返回:
            发送结果；发送失败不会向调用方抛出异常。
        """

        mail_id = mail.get("id")
        try:
            self.telegram_service.send_message(format_new_mail_message(mail))
        except Exception as exc:
            logger.warning(
                "新邮件 Telegram 通知失败：mailId=%s mailbox=%s error=%s",
                mail_id,
                mail.get("mailbox_email"),
                exc,
            )
            return {"sent": False, "error": str(exc)}

        logger.info(
            "新邮件 Telegram 通知已发送：mailId=%s mailbox=%s",
            mail_id,
            mail.get("mailbox_email"),
        )
        return {"sent": True}


mail_alert_service = MailAlertService()
