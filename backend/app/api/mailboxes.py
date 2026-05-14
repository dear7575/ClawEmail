import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.services.mailboxes import (
    CommunicationSettingsUpdate,
    MailboxCreate,
    mailbox_service,
)

router = APIRouter(tags=["mailboxes"])
logger = logging.getLogger(__name__)


@router.get("/api/mailboxes")
def list_mailboxes(
    connectionId: str | None = Query(default=None),
    sync: str | None = Query(default=None),
) -> dict[str, list[dict]]:
    """列出本地邮箱，可按参数触发 Claw Dashboard 同步。"""

    logger.info("API 查询邮箱列表：connection=%s sync=%s", connectionId or "all", sync == "true")
    return {"items": mailbox_service.list(connection_id=connectionId, sync=sync == "true")}


@router.post("/api/mailboxes", status_code=status.HTTP_201_CREATED)
def create_mailbox(body: MailboxCreate) -> dict:
    """创建 Claw 子邮箱。"""

    logger.info("API 创建邮箱：connection=%s suffix=%s", body.connectionId or "legacy", body.suffix)
    return mailbox_service.create(body)


@router.post("/api/mailboxes/{mailbox_id}/comm-settings")
def update_mailbox_comm_settings(mailbox_id: str, body: CommunicationSettingsUpdate) -> dict:
    """更新 Claw 邮箱通信设置。"""

    logger.info("API 更新邮箱通信设置：mailboxId=%s commLevel=%s", mailbox_id, body.commLevel)
    mailbox = mailbox_service.update_comm_settings(mailbox_id, body)
    if not mailbox:
        raise HTTPException(status_code=404, detail="mailbox not found")
    return mailbox


@router.delete("/api/mailboxes/{mailbox_id}")
def delete_mailbox(mailbox_id: str) -> dict[str, bool]:
    """删除 Claw 子邮箱。"""

    logger.info("API 删除邮箱：mailboxId=%s", mailbox_id)
    mailbox_service.delete(mailbox_id)
    return {"success": True}
