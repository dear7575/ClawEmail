import logging

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from app.services.mails import mail_service

router = APIRouter(tags=["mails"])
logger = logging.getLogger(__name__)


@router.get("/api/mails")
def list_mails(
    connectionId: str | None = Query(default=None),
    mailbox: str | None = Query(default=None),
    sync: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    keyword: str | None = Query(default=None, max_length=200),
) -> dict:
    """查询邮件列表，可按参数触发远端同步。"""

    logger.info(
        "API 查询邮件列表：connection=%s mailbox=%s sync=%s limit=%s offset=%s keyword=%s",
        connectionId or "all",
        mailbox or "all",
        sync == "true",
        limit,
        offset,
        bool(keyword),
    )
    return mail_service.list_with_optional_sync(
        connection_id=connectionId,
        mailbox_email=mailbox,
        limit=limit,
        offset=offset,
        keyword=keyword,
        sync=sync == "true",
    )


@router.get("/api/mails/{mail_id}")
def get_mail(mail_id: int) -> dict:
    """读取邮件详情并标记本地已读。"""

    detail = mail_service.detail(mail_id)
    if not detail:
        raise HTTPException(status_code=404, detail="mail not found")
    return detail


@router.get("/api/mails/{mail_id}/attachments/{part_id}")
def download_attachment(mail_id: int, part_id: str) -> Response:
    """下载邮件附件。"""

    attachment = mail_service.download_attachment(mail_id, part_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="mail not found")
    return Response(
        content=attachment.content,
        media_type=attachment.content_type,
        headers={
            "content-disposition": f'attachment; filename="{attachment.filename}"',
        },
    )


@router.delete("/api/mails")
def clear_mails(
    connectionId: str | None = Query(default=None),
    mailbox: str | None = Query(default=None),
) -> dict:
    """批量删除远端邮件并清理本地缓存。"""

    logger.info("API 批量删除邮件：connection=%s mailbox=%s", connectionId or "all", mailbox or "all")
    result = mail_service.clear_remote(connection_id=connectionId, mailbox_email=mailbox)
    if result["failed"]:
        return JSONResponse(status_code=status.HTTP_207_MULTI_STATUS, content=result)
    return result


@router.post("/api/mails/mark-read")
def mark_mails_read(
    connectionId: str | None = Query(default=None),
    mailbox: str | None = Query(default=None),
) -> dict[str, int | bool]:
    """批量将当前范围内的本地邮件标记为已读。"""

    logger.info("API 批量标记邮件已读：connection=%s mailbox=%s", connectionId or "all", mailbox or "all")
    return mail_service.mark_read(connection_id=connectionId, mailbox_email=mailbox)


@router.delete("/api/mails/{mail_id}")
def delete_mail(mail_id: int) -> dict[str, bool]:
    """删除单封远端邮件并清理本地缓存。"""

    logger.info("API 删除邮件：mailId=%s", mail_id)
    mail_service.delete_remote(mail_id)
    return {"success": True}
