import logging

from fastapi import APIRouter, HTTPException

from app.services.mails import ReplyMailBody, SendMailBody, mail_service

router = APIRouter(tags=["send"])
logger = logging.getLogger(__name__)


@router.post("/api/send")
def send_mail(body: SendMailBody) -> dict[str, str]:
    """发送新邮件。"""

    try:
        logger.info("API 发送邮件：from=%s toCount=%s", body.from_, len(body.to))
        return mail_service.send(body)
    except ValueError as exc:
        raise HTTPException(status_code=400 if "from mailbox" in str(exc) else 404, detail=str(exc)) from exc


@router.post("/api/reply")
def reply_mail(body: ReplyMailBody) -> dict[str, str]:
    """回复已有邮件。"""

    try:
        logger.info("API 回复邮件：mailId=%s toAll=%s", body.mailId, bool(body.toAll))
        return mail_service.reply(body)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
