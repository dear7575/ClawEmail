import logging
from typing import Any

from app.db.mail_repository import MailRepository, mail_repository
from app.services.telegram import TelegramService, telegram_service


logger = logging.getLogger(__name__)
OPENAI_DEACTIVATION_NEEDLES = (
    "account_deactivated",
    "openai api - access deactivated",
)


def compact_text(value: Any, limit: int = 500) -> str:
    """将可选字段转换为适合 Telegram 展示的单行文本。"""

    text = str(value or "").strip()
    if not text:
        return "-"
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


def mail_search_blob(mail: dict[str, Any]) -> str:
    """拼接用于告警匹配的邮件字段。"""

    fields = [
        mail.get("subject"),
        mail.get("text"),
        mail.get("html"),
        mail.get("header_raw"),
        mail.get("raw_json"),
    ]
    return "\n".join(str(item) for item in fields if item).lower()


def is_openai_deactivation_mail(mail: dict[str, Any]) -> bool:
    """判断邮件是否属于 OpenAI 账号或 API 访问停用通知。"""

    blob = mail_search_blob(mail)
    return any(needle in blob for needle in OPENAI_DEACTIVATION_NEEDLES)


def format_openai_deactivation_message(mail: dict[str, Any]) -> str:
    """生成 OpenAI 停用告警的 Telegram 文本。"""

    return "\n".join([
        "OpenAI 账号停用告警",
        "",
        f"收件邮箱：{compact_text(mail.get('mailbox_email'), 200)}",
        f"发件人：{compact_text(mail.get('source'), 300)}",
        f"主题：{compact_text(mail.get('subject'), 500)}",
        f"时间：{compact_text(mail.get('received_at') or mail.get('created_at'), 120)}",
        f"邮件ID：{compact_text(mail.get('provider_mail_id'), 120)}",
    ])


class MailAlertService:
    """邮件告警服务，负责识别特定邮件并触发外部通知。"""

    def __init__(
        self,
        repository: MailRepository = mail_repository,
        telegram_service_value: TelegramService = telegram_service,
    ) -> None:
        """初始化邮件告警服务。

        参数:
            repository: 邮件仓储，用于发送成功后标记本地邮件已读。
            telegram_service_value: Telegram 发送服务。
        """

        self.repository = repository
        self.telegram_service = telegram_service_value

    def notify_openai_deactivation_if_needed(self, mail: dict[str, Any]) -> dict[str, Any]:
        """在邮件命中 OpenAI 停用规则时发送 Telegram 并标记本地已读。

        参数:
            mail: 已保存到本地数据库的邮件记录。

        返回:
            告警匹配和发送结果；发送失败不会向调用方抛出异常。
        """

        mail_id = mail.get("id")
        if mail.get("read_at"):
            logger.debug("跳过已读邮件告警：mailId=%s", mail_id)
            return {"matched": False, "sent": False, "skipped": "read"}
        if not is_openai_deactivation_mail(mail):
            return {"matched": False, "sent": False}

        try:
            self.telegram_service.send_message(format_openai_deactivation_message(mail))
        except Exception as exc:
            logger.warning(
                "OpenAI 停用邮件 Telegram 告警失败：mailId=%s mailbox=%s error=%s",
                mail_id,
                mail.get("mailbox_email"),
                exc,
            )
            return {"matched": True, "sent": False, "error": str(exc)}

        if mail_id is not None:
            self.repository.mark_mail_read(int(mail_id))
        logger.info(
            "OpenAI 停用邮件 Telegram 告警已发送：mailId=%s mailbox=%s",
            mail_id,
            mail.get("mailbox_email"),
        )
        return {"matched": True, "sent": True}


mail_alert_service = MailAlertService()
