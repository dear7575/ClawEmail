import logging

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db.mail_repository import MailRepository, mail_repository
from app.services.claw_dashboard import ClawDashboardClient, claw_dashboard_client


logger = logging.getLogger(__name__)


class MailboxCreate(BaseModel):
    """子邮箱创建请求。"""

    connectionId: str | None = None
    suffix: str = Field(pattern=r"^[a-z0-9]{1,32}$")


class CommunicationSettingsUpdate(BaseModel):
    """邮箱通信设置更新请求。"""

    commLevel: int = Field(ge=0, le=2)
    extReceiveType: int | None = Field(default=None, ge=0, le=1)
    extSendType: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_external_settings(self):
        """校验外部收发模式必须同时提供接收和发送类型。"""

        if self.commLevel == 2 and (self.extReceiveType is None or self.extSendType is None):
            raise ValueError("extReceiveType and extSendType are required when commLevel is 2")
        return self


class MailboxService:
    """迁移版邮箱服务，复用 Claw Dashboard HTTP 操作并写回本地 SQLite。"""

    def __init__(
        self,
        repository: MailRepository = mail_repository,
        dashboard: ClawDashboardClient = claw_dashboard_client,
    ) -> None:
        """初始化邮箱服务。"""

        self.repository = repository
        self.dashboard = dashboard

    def list(self, connection_id: str | None = None, sync: bool = False) -> list[dict]:
        """列出本地邮箱，必要时先从 Claw Dashboard 同步。

        参数:
            connection_id: Claw 连接 ID；为空时列出所有连接的邮箱。
            sync: 是否先拉取远端邮箱快照。

        返回:
            本地邮箱列表。
        """

        if sync:
            logger.info("开始同步 Claw 邮箱列表：connection=%s", connection_id or "all")
            remote_mailboxes = self.dashboard.list_mailboxes(connection_id=connection_id)
            for mailbox in remote_mailboxes:
                self.repository.upsert_mailbox({
                    "id": mailbox["id"],
                    "connection_id": connection_id,
                    "provider_mailbox_id": mailbox["id"],
                    "email": mailbox["email"],
                    "prefix": mailbox["prefix"],
                    "display_name": mailbox.get("display_name"),
                    "status": mailbox.get("status") or "active",
                    "openclaw_status": mailbox.get("openclaw_status"),
                    "install_command": mailbox.get("install_command"),
                    "auth_url": mailbox.get("auth_url"),
                    "comm_level": mailbox.get("comm_level"),
                    "ext_receive_type": mailbox.get("ext_receive_type"),
                    "ext_send_type": mailbox.get("ext_send_type"),
                })
            logger.info("Claw 邮箱列表同步完成：connection=%s count=%s", connection_id or "all", len(remote_mailboxes))
        mailboxes = self.repository.list_mailboxes(connection_id=connection_id)
        logger.debug("读取本地邮箱列表：connection=%s count=%s", connection_id or "all", len(mailboxes))
        return mailboxes

    def create(self, body: MailboxCreate) -> dict:
        """创建 Claw 子邮箱并默认开启外部收发。

        参数:
            body: 连接 ID 和子邮箱后缀。

        返回:
            本地保存后的邮箱记录。
        """

        logger.info("开始创建邮箱：connection=%s suffix=%s", body.connectionId or "legacy", body.suffix)
        remote = self.dashboard.create_mailbox(body.suffix, body.connectionId)
        # 新建子邮箱后立即打开外部收发，保持与旧 Node 版本的默认行为一致。
        self.dashboard.update_mailbox_comm_settings(remote["id"], {
            "commLevel": 2,
            "extReceiveType": 1,
            "extSendType": 1,
        }, body.connectionId)
        saved = self.repository.upsert_mailbox({
            "id": remote["id"],
            "connection_id": body.connectionId,
            "provider_mailbox_id": remote["id"],
            "email": remote["email"],
            "prefix": remote["prefix"],
            "display_name": remote.get("display_name"),
            "status": remote.get("status") or "active",
            "openclaw_status": remote.get("openclaw_status"),
            "install_command": remote.get("install_command"),
            "auth_url": remote.get("auth_url"),
            "status": "active",
            "comm_level": 2,
            "ext_receive_type": 1,
            "ext_send_type": 1,
        })
        logger.info("邮箱创建完成：mailboxId=%s email=%s", saved["id"], saved["email"])
        return saved

    def update_comm_settings(self, mailbox_id: str, body: CommunicationSettingsUpdate) -> dict | None:
        """更新邮箱通信设置并写回本地缓存。

        参数:
            mailbox_id: 本地邮箱 ID。
            body: 通信设置请求。

        返回:
            更新后的本地邮箱；邮箱不存在时返回 None。
        """

        mailbox = self.repository.get_mailbox(mailbox_id)
        if not mailbox:
            logger.warning("更新邮箱通信设置失败，邮箱不存在：mailboxId=%s", mailbox_id)
            return None
        dashboard_payload = (
            {
                "commLevel": body.commLevel,
                "extReceiveType": body.extReceiveType,
                "extSendType": body.extSendType,
            }
            if body.commLevel == 2
            else {"commLevel": body.commLevel}
        )
        self.dashboard.update_mailbox_comm_settings(
            mailbox.get("provider_mailbox_id") or mailbox_id,
            dashboard_payload,
            mailbox.get("connection_id"),
        )
        updated = self.repository.update_mailbox_comm_settings(mailbox_id, {
            "comm_level": body.commLevel,
            "ext_receive_type": body.extReceiveType if body.commLevel == 2 else None,
            "ext_send_type": body.extSendType if body.commLevel == 2 else None,
        })
        logger.info("邮箱通信设置更新完成：mailboxId=%s commLevel=%s", mailbox_id, body.commLevel)
        return updated

    def delete(self, mailbox_id: str) -> bool:
        """删除远端邮箱并标记本地邮箱为 deleted。

        参数:
            mailbox_id: 本地邮箱 ID。

        返回:
            是否成功完成本地删除标记；邮箱不存在时视为成功。
        """

        mailbox = self.repository.get_mailbox(mailbox_id)
        if not mailbox:
            logger.warning("删除邮箱时本地记录不存在，按幂等成功处理：mailboxId=%s", mailbox_id)
            return True
        logger.info("开始删除邮箱：mailboxId=%s email=%s", mailbox_id, mailbox.get("email"))
        self.dashboard.delete_mailbox(
            mailbox.get("provider_mailbox_id") or mailbox_id,
            mailbox.get("connection_id"),
        )
        deleted = self.repository.mark_mailbox_deleted(mailbox_id)
        logger.info("邮箱删除完成：mailboxId=%s success=%s", mailbox_id, deleted)
        return deleted


mailbox_service = MailboxService()
