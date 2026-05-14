from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.db.claw_repository import ClawRepository, claw_repository
from app.services.network_settings import NetworkSettingsService, network_settings_service


logger = logging.getLogger(__name__)
CLAW_ORIGIN = "https://claw.163.com"
TOKEN_URL = f"{CLAW_ORIGIN}/claw-api-gateway/open/v1/mail/auth/token"
COREMAIL_BASE_URL = f"{CLAW_ORIGIN}/claw-api-gateway/api/coremail"
TOKEN_REFRESH_SKEW_SECONDS = 60
FOLDER_IDS = {
    "INBOX": 1,
    "Inbox": 1,
    "inbox": 1,
    "收件箱": 1,
    "Drafts": 2,
    "Draft": 2,
    "草稿箱": 2,
    "草稿": 2,
    "Sent": 3,
    "Sent Items": 3,
    "已发送": 3,
    "Trash": 4,
    "Deleted": 4,
    "已删除": 4,
    "垃圾箱": 4,
    "Spam": 5,
    "Junk": 5,
    "垃圾邮件": 5,
    "广告邮件": 5,
}


@dataclass
class AttachmentDownload:
    """附件下载结果，供 FastAPI 路由直接写入 HTTP 响应。"""

    filename: str
    content_type: str
    content: bytes
    size: int | None = None


def folder_id(value: str | int) -> int:
    """将 Coremail 文件夹别名或数字字符串归一化为文件夹 ID。

    参数:
        value: 文件夹名称、别名或数字 ID。

    返回:
        Coremail 接口要求的数字文件夹 ID。

    异常:
        ValueError: 传入值无法解析为有效文件夹 ID。
    """

    if isinstance(value, int):
        return value
    if value in FOLDER_IDS:
        return FOLDER_IDS[value]
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无效的邮箱文件夹 ID：{value}") from exc


def parse_coremail_response(body: Any) -> Any:
    """解析 Coremail 代理接口响应并抽取业务数据。

    参数:
        body: Coremail 返回的 JSON 对象。

    返回:
        响应中的 var 字段。

    异常:
        RuntimeError: 响应结构异常或业务 code 不是 S_OK。
    """

    if not isinstance(body, dict) or not isinstance(body.get("code"), str):
        raise RuntimeError(f"Claw 邮件接口返回格式异常：{body}")
    if body["code"] != "S_OK":
        raise RuntimeError(str(body.get("message") or body["code"]))
    return body.get("var")


def attachment_list(mail: dict[str, Any]) -> list[dict[str, Any]]:
    """从 Coremail 邮件详情中提取可持久化的附件列表。

    参数:
        mail: Coremail 邮件详情对象。

    返回:
        附件元数据列表，字段名匹配本地仓储入参。
    """

    return [
        {
            "provider_part_id": str(attachment.get("id")),
            "filename": attachment.get("filename"),
            "content_type": attachment.get("contentType") or "application/octet-stream",
            "size": attachment.get("size") or attachment.get("contentLength"),
        }
        for attachment in mail.get("attachments") or []
        if isinstance(attachment, dict) and attachment.get("id") is not None
    ]


class ClawMailClient:
    """Claw 邮件 Coremail 代理客户端，复刻官方 Node SDK 的 HTTP 调用。"""

    def __init__(
        self,
        repository: ClawRepository = claw_repository,
        network_service: NetworkSettingsService = network_settings_service,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        """初始化 Claw 邮件客户端和 token 缓存。"""

        self.repository = repository
        self.network_service = network_service
        self.client_factory = client_factory
        self._tokens: dict[tuple[str, str], tuple[str, float]] = {}

    def api_key(self, connection_id: str | None = None) -> str:
        """读取指定 Claw 连接的 API Key。

        参数:
            connection_id: Claw 连接 ID；为空时回退到 legacy 连接。

        返回:
            可用于 Claw Open API 的 API Key。

        异常:
            ValueError: 当前连接未配置 API Key。
        """

        connection = self.repository.resolve_connection(connection_id)
        api_key = connection.get("api_key") if connection else None
        if not api_key:
            raise ValueError("CLAW_API_KEY is required for mail operations; connect Claw first")
        return str(api_key)

    def client_options(self) -> dict[str, Any]:
        """构造 httpx 客户端选项，统一应用系统代理和超时设置。

        返回:
            可传给 httpx.Client 的 timeout/proxy 选项。
        """

        settings = self.network_service.get()
        return {
            "timeout": settings.timeout_ms / 1000,
            "proxy": settings.proxy_url or None,
        }

    def ensure_token(self, uid: str, connection_id: str | None = None) -> str:
        """获取并缓存 Coremail access token。

        参数:
            uid: Claw 邮箱地址。
            connection_id: Claw 连接 ID；为空时使用 legacy 连接。

        返回:
            Coremail 代理接口所需的 access token。

        异常:
            RuntimeError: Token 接口返回非 JSON、失败状态或缺少关键字段。
            ValueError: 连接未配置 API Key。
        """

        api_key = self.api_key(connection_id)
        key = (connection_id or "legacy", uid)
        cached = self._tokens.get(key)
        if cached and cached[1] - time.time() > TOKEN_REFRESH_SKEW_SECONDS:
            logger.debug("复用 Claw 邮件 token：uid=%s connection=%s", uid, connection_id or "legacy")
            return cached[0]
        logger.info("获取 Claw 邮件 token：uid=%s connection=%s", uid, connection_id or "legacy")
        with self.client_factory(**self.client_options()) as client:
            response = client.post(
                TOKEN_URL,
                headers={
                    "authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                },
                json={"uid": uid},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Claw 邮件 token 接口返回非 JSON：HTTP {response.status_code}") from exc
        result = body.get("result") if isinstance(body, dict) else None
        access_token = result.get("accessToken") if isinstance(result, dict) else None
        expires_in = result.get("expiresIn") if isinstance(result, dict) else None
        if not response.is_success or not access_token or not expires_in:
            logger.error("获取 Claw 邮件 token 失败：uid=%s status=%s", uid, response.status_code)
            raise RuntimeError(f"获取 Claw 邮件 access token 失败：{body}")
        self._tokens[key] = (str(access_token), time.time() + float(expires_in))
        logger.info("获取 Claw 邮件 token 成功：uid=%s status=%s", uid, response.status_code)
        return str(access_token)

    def coremail_call(
        self,
        uid: str,
        func: str,
        payload: dict[str, Any] | None = None,
        connection_id: str | None = None,
    ) -> Any:
        """调用 Claw Coremail 代理接口。

        参数:
            uid: Claw 邮箱地址。
            func: Coremail 函数名，例如 mbox:listMessages。
            payload: Coremail 函数参数。
            connection_id: Claw 连接 ID；为空时使用 legacy 连接。

        返回:
            Coremail 业务响应中的 var 字段。

        异常:
            RuntimeError: HTTP 请求失败、响应非 JSON 或业务响应失败。
        """

        token = self.ensure_token(uid, connection_id)
        logger.debug("调用 Claw Coremail：uid=%s func=%s connection=%s", uid, func, connection_id or "legacy")
        with self.client_factory(**self.client_options()) as client:
            response = client.post(
                f"{COREMAIL_BASE_URL}/proxy",
                params={"uid": uid, "func": func},
                headers={"authorization": f"Bearer {token}"},
                json=payload or {},
            )
        try:
            body = response.json()
        except ValueError as exc:
            logger.error("Claw Coremail 返回非 JSON：uid=%s func=%s status=%s", uid, func, response.status_code)
            raise RuntimeError(f"Claw 邮件接口返回非 JSON：HTTP {response.status_code}") from exc
        if not response.is_success:
            logger.error("Claw Coremail 请求失败：uid=%s func=%s status=%s", uid, func, response.status_code)
            raise RuntimeError(f"Claw 邮件接口请求失败：{body}")
        logger.debug("Claw Coremail 调用成功：uid=%s func=%s status=%s", uid, func, response.status_code)
        return parse_coremail_response(body)

    def list_inbox_message_ids(
        self,
        mailbox_email: str,
        max_messages: int = 500,
        connection_id: str | None = None,
    ) -> list[str]:
        """分页读取收件箱远端邮件 ID。

        参数:
            mailbox_email: Claw 邮箱地址。
            max_messages: 最多读取的邮件数量。
            connection_id: Claw 连接 ID；为空时使用 legacy 连接。

        返回:
            远端 provider_mail_id 列表，按 Coremail 返回顺序排列。
        """

        logger.info("开始同步收件箱 ID：mailbox=%s max=%s", mailbox_email, max_messages)
        ids: list[str] = []
        page_size = 100
        for start in range(0, max_messages, page_size):
            messages = self.coremail_call(
                mailbox_email,
                "mbox:listMessages",
                {
                    "fid": folder_id("INBOX"),
                    "order": "date",
                    "desc": True,
                    "start": start,
                    "limit": min(page_size, max_messages - start),
                },
                connection_id,
            ) or []
            for message in messages:
                if isinstance(message, dict) and message.get("id"):
                    ids.append(str(message["id"]))
            if len(messages) < page_size:
                break
        logger.info("收件箱 ID 同步完成：mailbox=%s count=%s", mailbox_email, len(ids))
        return ids

    def read_mail(
        self,
        mailbox_email: str,
        provider_mail_id: str,
        connection_id: str | None = None,
        mark_read: bool = False,
    ) -> dict[str, Any]:
        """读取单封远端邮件详情并转换为本地统一结构。

        参数:
            mailbox_email: Claw 邮箱地址。
            provider_mail_id: Coremail 邮件 ID。
            connection_id: Claw 连接 ID；为空时使用 legacy 连接。
            mark_read: 是否在远端标记已读。

        返回:
            邮件详情对象，包含正文、头信息和附件元数据。
        """

        logger.debug("读取远端邮件：mailbox=%s providerMailId=%s", mailbox_email, provider_mail_id)
        raw = self.coremail_call(
            mailbox_email,
            "mbox:readMessage",
            {
                "id": provider_mail_id,
                "mode": "html",
                "markRead": mark_read,
                "header": True,
                "securityLevel": 1,
                "filterLinks": False,
                "filterImages": False,
            },
            connection_id,
        ) or {}
        result: dict[str, Any] = {
            "id": provider_mail_id,
            "from": raw.get("from"),
            "to": raw.get("to"),
            "cc": raw.get("cc"),
            "bcc": raw.get("bcc"),
            "subject": raw.get("subject"),
            "date": raw.get("sentDate"),
            "priority": raw.get("priority"),
            "headerRaw": raw.get("headerRaw"),
        }
        if isinstance(raw.get("text"), dict):
            result["text"] = {"content": raw["text"].get("content")}
        if isinstance(raw.get("html"), dict):
            result["html"] = {"content": raw["html"].get("content")}
        attachments = []
        for attachment in raw.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            attachments.append({
                "id": str(attachment.get("id")),
                "filename": attachment.get("filename"),
                "contentType": attachment.get("contentType") or "application/octet-stream",
                "size": attachment.get("contentLength"),
                "inline": attachment.get("inlined"),
                "contentId": attachment.get("contentId"),
            })
        if attachments:
            result["attachments"] = attachments
        logger.debug(
            "远端邮件读取完成：mailbox=%s providerMailId=%s attachments=%s",
            mailbox_email,
            provider_mail_id,
            len(attachments),
        )
        return result

    def send_mail(
        self,
        mailbox_email: str,
        input_value: dict[str, Any],
        connection_id: str | None = None,
    ) -> dict[str, str]:
        """通过 Coremail 草稿投递流程发送新邮件。

        参数:
            mailbox_email: 发件 Claw 邮箱地址。
            input_value: 收件人、抄送、主题、正文等发送参数。
            connection_id: Claw 连接 ID；为空时使用 legacy 连接。

        返回:
            发送状态。

        异常:
            RuntimeError: compose 未返回草稿 ID 或投递失败。
        """

        logger.info(
            "开始发送邮件：from=%s toCount=%s ccCount=%s bccCount=%s",
            mailbox_email,
            len(input_value.get("to") or []),
            len(input_value.get("cc") or []),
            len(input_value.get("bcc") or []),
        )
        compose_attrs = {
            "to": input_value["to"],
            "subject": input_value.get("subject") or "",
            "content": input_value.get("body") or "",
            "isHtml": bool(input_value.get("html")),
            "priority": 3,
            "saveSentCopy": True,
            "account": mailbox_email,
        }
        if input_value.get("cc"):
            compose_attrs["cc"] = input_value["cc"]
        if input_value.get("bcc"):
            compose_attrs["bcc"] = input_value["bcc"]
        compose_id = self.coremail_call(
            mailbox_email,
            "mbox:compose",
            {"action": "continue", "attrs": compose_attrs},
            connection_id,
        )
        resolved_compose_id = compose_id if isinstance(compose_id, str) else compose_id.get("id") if isinstance(compose_id, dict) else None
        if not resolved_compose_id:
            logger.error("Claw 邮件 compose 未返回草稿 ID：from=%s", mailbox_email)
            raise RuntimeError("Claw 邮件 compose 未返回草稿 ID")
        # Coremail 发送必须先创建/续写草稿，再用同一个 compose_id 执行 deliver。
        self.coremail_call(
            mailbox_email,
            "mbox:compose",
            {"id": resolved_compose_id, "action": "deliver", "attrs": compose_attrs},
            connection_id,
        )
        logger.info("邮件发送成功：from=%s composeId=%s", mailbox_email, resolved_compose_id)
        return {"status": "sent"}

    def reply_mail(
        self,
        mailbox_email: str,
        provider_mail_id: str,
        input_value: dict[str, Any],
        connection_id: str | None = None,
    ) -> dict[str, str]:
        """回复指定远端邮件。

        参数:
            mailbox_email: Claw 邮箱地址。
            provider_mail_id: 被回复邮件的 Coremail ID。
            input_value: 回复正文和回复全部标记。
            connection_id: Claw 连接 ID；为空时使用 legacy 连接。

        返回:
            发送状态。
        """

        logger.info("开始回复邮件：mailbox=%s providerMailId=%s", mailbox_email, provider_mail_id)
        attrs = {
            "content": input_value.get("body") or "",
            "isHtml": bool(input_value.get("html")),
            "saveSentCopy": True,
        }
        self.coremail_call(
            mailbox_email,
            "mbox:replyMessage",
            {
                "id": provider_mail_id,
                "toAll": bool(input_value.get("toAll")),
                "withAttachments": False,
                "action": "deliver",
                "attrs": attrs,
            },
            connection_id,
        )
        logger.info("邮件回复成功：mailbox=%s providerMailId=%s", mailbox_email, provider_mail_id)
        return {"status": "sent"}

    def delete_mail(self, mailbox_email: str, provider_mail_id: str, connection_id: str | None = None) -> None:
        """将远端邮件移动到垃圾箱。

        参数:
            mailbox_email: Claw 邮箱地址。
            provider_mail_id: Coremail 邮件 ID。
            connection_id: Claw 连接 ID；为空时使用 legacy 连接。
        """

        logger.info("删除远端邮件：mailbox=%s providerMailId=%s", mailbox_email, provider_mail_id)
        self.coremail_call(
            mailbox_email,
            "mbox:updateMessageInfos",
            {"ids": [provider_mail_id], "attrs": {"fid": folder_id("Trash")}},
            connection_id,
        )

    def download_attachment(
        self,
        mailbox_email: str,
        provider_mail_id: str,
        part_id: str,
        connection_id: str | None = None,
    ) -> AttachmentDownload:
        """下载指定邮件附件。

        参数:
            mailbox_email: Claw 邮箱地址。
            provider_mail_id: Coremail 邮件 ID。
            part_id: Coremail 附件 part ID。
            connection_id: Claw 连接 ID；为空时使用 legacy 连接。

        返回:
            附件文件名、内容类型、字节内容和大小。

        异常:
            RuntimeError: 远端附件下载失败。
        """

        logger.info("开始下载附件：mailbox=%s providerMailId=%s partId=%s", mailbox_email, provider_mail_id, part_id)
        token = self.ensure_token(mailbox_email, connection_id)
        with self.client_factory(**self.client_options()) as client:
            response = client.get(
                f"{COREMAIL_BASE_URL}/proxy",
                params={
                    "uid": mailbox_email,
                    "func": "mbox:getMessageData",
                    "mid": provider_mail_id,
                    "part": part_id,
                    "mode": "download",
                },
                headers={"authorization": f"Bearer {token}"},
        )
        if not response.is_success:
            logger.error(
                "附件下载失败：mailbox=%s providerMailId=%s partId=%s status=%s",
                mailbox_email,
                provider_mail_id,
                part_id,
                response.status_code,
            )
            raise RuntimeError(f"Claw 附件下载失败：HTTP {response.status_code}")
        disposition = response.headers.get("content-disposition", "")
        filename = f"attachment_{part_id}"
        if "filename=" in disposition:
            filename = disposition.split("filename=", 1)[1].strip("\"'; ")
        result = AttachmentDownload(
            filename=filename,
            content_type=response.headers.get("content-type") or "application/octet-stream",
            content=response.content,
            size=int(response.headers["content-length"]) if response.headers.get("content-length") else None,
        )
        logger.info(
            "附件下载成功：mailbox=%s providerMailId=%s partId=%s filename=%s size=%s",
            mailbox_email,
            provider_mail_id,
            part_id,
            result.filename,
            result.size,
        )
        return result


def mail_to_repository_input(mailbox_email: str, mail: dict[str, Any], connection_id: str | None = None) -> dict[str, Any]:
    """将 Coremail 邮件详情转换为本地邮件仓储入参。

    参数:
        mailbox_email: 邮件所属 Claw 邮箱地址。
        mail: Coremail 邮件详情对象。
        connection_id: Claw 连接 ID；为空时使用 legacy 连接。

    返回:
        MailRepository.save_mail 可直接保存的数据结构。
    """

    return {
        "connection_id": connection_id,
        "provider_mail_id": str(mail["id"]),
        "mailbox_email": mailbox_email,
        "source": next(iter(mail.get("from") or []), None),
        "address": next(iter(mail.get("to") or []), mailbox_email),
        "subject": mail.get("subject"),
        "text": mail.get("text", {}).get("content") if isinstance(mail.get("text"), dict) else None,
        "html": mail.get("html", {}).get("content") if isinstance(mail.get("html"), dict) else None,
        "raw_json": json.dumps(mail, ensure_ascii=False),
        "header_raw": mail.get("headerRaw"),
        "has_attachments": bool(mail.get("attachments")),
        "received_at": mail.get("date"),
        "attachments": attachment_list(mail),
    }


claw_mail_client = ClawMailClient()
