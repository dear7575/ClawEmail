import logging

from pydantic import BaseModel, Field, field_validator

from app.db.mail_repository import MailRepository, mail_repository, parse_mail_raw_json
from app.services.claw_mail import ClawMailClient, claw_mail_client, mail_to_repository_input


logger = logging.getLogger(__name__)


class SendMailBody(BaseModel):
    """邮件发送请求。"""

    from_: str = Field(alias="from")
    to: list[str] = Field(min_length=1)
    cc: list[str] | None = None
    bcc: list[str] | None = None
    subject: str | None = None
    body: str | None = None
    html: bool | None = None

    @field_validator("from_")
    @classmethod
    def normalize_sender(cls, value: str) -> str:
        """归一化并校验发件邮箱。"""

        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("from must be an email address")
        return normalized

    @field_validator("to", "cc", "bcc")
    @classmethod
    def normalize_recipients(cls, value: list[str] | None) -> list[str] | None:
        """归一化并校验收件人、抄送和密送地址列表。"""

        if value is None:
            return None
        normalized = [str(item).strip().lower() for item in value]
        if any("@" not in item for item in normalized):
            raise ValueError("recipient must be an email address")
        return normalized


class ReplyMailBody(BaseModel):
    """邮件回复请求。"""

    mailId: int = Field(gt=0)
    body: str | None = None
    html: bool | None = None
    toAll: bool | None = None


class MailService:
    """迁移版邮件服务，负责本地缓存、远端同步和发信操作。"""

    def __init__(
        self,
        repository: MailRepository = mail_repository,
        mail_client: ClawMailClient = claw_mail_client,
    ) -> None:
        """初始化邮件服务。"""

        self.repository = repository
        self.mail_client = mail_client

    def list(
        self,
        connection_id: str | None,
        mailbox_email: str | None,
        limit: int,
        offset: int,
        keyword: str | None = None,
    ) -> dict:
        """从本地缓存分页读取邮件列表。"""

        normalized_mailbox = mailbox_email.strip().lower() if mailbox_email else None
        return self.repository.list_mails(
            connection_id=connection_id,
            mailbox_email=normalized_mailbox,
            limit=limit,
            offset=offset,
            keyword=keyword.strip() if keyword else None,
        )

    def detail(self, mail_id: int) -> dict | None:
        """读取邮件详情并将本地邮件标记为已读。"""

        mail = self.repository.mark_mail_read(mail_id)
        if not mail:
            return None
        return {
            **mail,
            "parsed": parse_mail_raw_json(mail),
            "attachments": self.repository.list_attachments(mail_id),
        }

    def sync_mailbox_inbox(self, connection_id: str | None, mailbox_email: str) -> None:
        """同步单个 Claw 邮箱收件箱。

        参数:
            connection_id: Claw 连接 ID；为空时使用 legacy 连接。
            mailbox_email: 要同步的 Claw 邮箱地址。
        """

        normalized_mailbox = mailbox_email.strip().lower()
        logger.info("开始同步邮箱收件箱：connection=%s mailbox=%s", connection_id or "legacy", normalized_mailbox)
        remote_ids = self.mail_client.list_inbox_message_ids(
            normalized_mailbox,
            max_messages=500,
            connection_id=connection_id,
        )
        remote_id_set = set(remote_ids)
        local_ids = self.repository.list_mail_provider_ids(normalized_mailbox, connection_id)
        removed = self.repository.delete_mails_by_provider_ids(
            normalized_mailbox,
            [provider_id for provider_id in local_ids if provider_id not in remote_id_set],
            connection_id,
        )
        saved = 0
        for provider_mail_id in remote_ids:
            if self.repository.get_mail_by_provider_id(normalized_mailbox, provider_mail_id, connection_id):
                continue
            mail = self.mail_client.read_mail(
                normalized_mailbox,
                provider_mail_id,
                connection_id=connection_id,
                mark_read=False,
            )
            self.repository.save_mail(mail_to_repository_input(normalized_mailbox, mail, connection_id))
            saved += 1
        logger.info(
            "邮箱收件箱同步完成：connection=%s mailbox=%s remote=%s localRemoved=%s saved=%s",
            connection_id or "legacy",
            normalized_mailbox,
            len(remote_ids),
            removed,
            saved,
        )

    def sync_all_mailbox_inboxes(self, connection_id: str | None = None) -> None:
        """同步指定连接下所有本地活跃邮箱的收件箱。"""

        mailboxes = self.repository.list_active_mailboxes(connection_id)
        logger.info("开始同步全部邮箱收件箱：connection=%s mailboxCount=%s", connection_id or "all", len(mailboxes))
        for mailbox in mailboxes:
            self.sync_mailbox_inbox(mailbox.get("connection_id") or connection_id, mailbox["email"])
        logger.info("全部邮箱收件箱同步完成：connection=%s mailboxCount=%s", connection_id or "all", len(mailboxes))

    def list_with_optional_sync(
        self,
        connection_id: str | None,
        mailbox_email: str | None,
        limit: int,
        offset: int,
        keyword: str | None = None,
        sync: bool = False,
    ) -> dict:
        """按请求参数决定是否先同步远端，再返回本地邮件列表。"""

        normalized_mailbox = mailbox_email.strip().lower() if mailbox_email else None
        if sync and normalized_mailbox:
            self.sync_mailbox_inbox(connection_id, normalized_mailbox)
        elif sync:
            self.sync_all_mailbox_inboxes(connection_id)
        return self.list(connection_id, normalized_mailbox, limit, offset, keyword)

    def delete_remote(self, mail_id: int) -> bool:
        """删除单封远端邮件并删除本地缓存。

        参数:
            mail_id: 本地邮件 ID。

        返回:
            本地删除是否成功；邮件不存在时按幂等成功处理。
        """

        mail = self.repository.get_mail(mail_id)
        if not mail:
            logger.warning("删除邮件时本地记录不存在，按幂等成功处理：mailId=%s", mail_id)
            return True
        logger.info("开始删除邮件：mailId=%s mailbox=%s providerMailId=%s", mail_id, mail["mailbox_email"], mail["provider_mail_id"])
        self.mail_client.delete_mail(mail["mailbox_email"], mail["provider_mail_id"], mail.get("connection_id"))
        deleted = self.repository.delete_mail(mail_id)
        logger.info("邮件删除完成：mailId=%s success=%s", mail_id, deleted)
        return deleted

    def clear_remote(self, connection_id: str | None = None, mailbox_email: str | None = None) -> dict:
        """批量删除远端邮件并清理本地缓存。

        参数:
            connection_id: 可选 Claw 连接 ID。
            mailbox_email: 可选邮箱过滤条件。

        返回:
            删除成功/失败数量及失败明细。
        """

        normalized_mailbox = mailbox_email.strip().lower() if mailbox_email else None
        mails = self.repository.list_mails_for_deletion(connection_id=connection_id, mailbox_email=normalized_mailbox)
        logger.info(
            "开始批量删除邮件：connection=%s mailbox=%s total=%s",
            connection_id or "all",
            normalized_mailbox or "all",
            len(mails),
        )
        errors: list[dict] = []
        deleted = 0
        for mail in mails:
            try:
                self.mail_client.delete_mail(mail["mailbox_email"], mail["provider_mail_id"], mail.get("connection_id"))
                if self.repository.delete_mail(mail["id"]):
                    deleted += 1
            except Exception as exc:
                logger.error(
                    "批量删除邮件失败：mailId=%s mailbox=%s providerMailId=%s error=%s",
                    mail["id"],
                    mail["mailbox_email"],
                    mail["provider_mail_id"],
                    exc,
                )
                errors.append({
                    "id": mail["id"],
                    "mailboxEmail": mail["mailbox_email"],
                    "providerMailId": mail["provider_mail_id"],
                    "error": str(exc),
                })
        result = {
            "success": not errors,
            "deleted": deleted,
            "failed": len(errors),
            "errors": errors,
        }
        logger.info(
            "批量删除邮件完成：connection=%s mailbox=%s deleted=%s failed=%s",
            connection_id or "all",
            normalized_mailbox or "all",
            deleted,
            len(errors),
        )
        return result

    def send(self, body: SendMailBody) -> dict[str, str]:
        """发送新邮件。"""

        mailbox_email = str(body.from_).strip().lower()
        mailbox = self.repository.get_mailbox_by_email(mailbox_email)
        if not mailbox:
            logger.warning("发送邮件失败，发件邮箱不受管理：from=%s", mailbox_email)
            raise ValueError("from mailbox is not managed by this app")
        logger.info("准备发送邮件：from=%s toCount=%s", mailbox_email, len(body.to))
        return self.mail_client.send_mail(
            mailbox_email,
            {
                "to": body.to,
                "cc": body.cc,
                "bcc": body.bcc,
                "subject": body.subject,
                "body": body.body,
                "html": body.html,
            },
            mailbox.get("connection_id"),
        )

    def reply(self, body: ReplyMailBody) -> dict[str, str]:
        """回复本地缓存中的邮件。"""

        mail = self.repository.get_mail(body.mailId)
        if not mail:
            logger.warning("回复邮件失败，邮件不存在：mailId=%s", body.mailId)
            raise ValueError("mail not found")
        logger.info("准备回复邮件：mailId=%s mailbox=%s", body.mailId, mail["mailbox_email"])
        return self.mail_client.reply_mail(
            mail["mailbox_email"],
            mail["provider_mail_id"],
            {
                "body": body.body,
                "html": body.html,
                "toAll": body.toAll,
            },
            mail.get("connection_id"),
        )

    def download_attachment(self, mail_id: int, part_id: str):
        """下载本地邮件对应的远端附件。"""

        mail = self.repository.get_mail(mail_id)
        if not mail:
            logger.warning("下载附件失败，邮件不存在：mailId=%s partId=%s", mail_id, part_id)
            return None
        logger.info("准备下载附件：mailId=%s partId=%s mailbox=%s", mail_id, part_id, mail["mailbox_email"])
        return self.mail_client.download_attachment(
            mail["mailbox_email"],
            mail["provider_mail_id"],
            part_id,
            mail.get("connection_id"),
        )


mail_service = MailService()
