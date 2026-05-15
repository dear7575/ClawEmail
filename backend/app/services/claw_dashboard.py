import logging
import re
from collections.abc import Callable
from typing import Any

import httpx

from app.db.claw_repository import ClawRepository, claw_repository


logger = logging.getLogger(__name__)
DASHBOARD_ORIGIN = "https://claw.163.com"
BASE_URL = f"{DASHBOARD_ORIGIN}/mailserv-claw-dashboard/api/v1"
PUBLIC_BASE_URL = f"{DASHBOARD_ORIGIN}/mailserv-claw-dashboard/p/v1"


def normalize_login_email(email: str) -> str:
    """归一化 Claw 登录邮箱，允许用户只输入 163 邮箱前缀。"""

    normalized = email.strip().replace("＠", "@").lower()
    return normalized if "@" in normalized else f"{normalized}@163.com"


def validate_login_email(email: str) -> str:
    """校验 Claw 登录邮箱必须是完整 163 邮箱。"""

    normalized = normalize_login_email(email)
    if not re.fullmatch(r"[^\s@]+@163\.com", normalized):
        raise ValueError("请输入完整 163 登录邮箱")
    return normalized


def dashboard_error_message(message: str) -> str:
    """将 Claw Dashboard 原始错误转换为中文用户提示。"""

    return "请输入完整 163 登录邮箱" if message == "invalid email format" else message


def cookie_header_from_set_cookie(headers: list[str]) -> str:
    """从 Set-Cookie 响应头中提取可直接复用的 Cookie 片段。"""

    return "; ".join(
        item
        for item in (header.split(";")[0].strip() for header in headers)
        if item
    )


def merge_cookie_headers(headers: list[str | None]) -> str:
    """合并多个 Cookie 头，保留登录流程中的临时和最终 Cookie。"""

    return "; ".join(
        part.strip()
        for header in headers
        for part in (header or "").split(";")
        if part.strip()
    )


def read_set_cookie(response: httpx.Response) -> str:
    """读取 httpx 响应中的 Set-Cookie 头。"""

    values = response.headers.get_list("set-cookie")
    if values:
        return cookie_header_from_set_cookie(values)
    single = response.headers.get("set-cookie")
    return cookie_header_from_set_cookie([single]) if single else ""


def parse_dashboard_response(response: httpx.Response) -> Any:
    """解析 Claw Dashboard 统一响应结构。

    参数:
        response: Claw Dashboard HTTP 响应。

    返回:
        result 字段内容；空成功响应返回 None。

    异常:
        RuntimeError: HTTP 失败、响应非 JSON 或业务 success/code 异常。
    """

    if not response.text.strip():
        if not response.is_success:
            logger.error("Claw Dashboard 空响应失败：status=%s", response.status_code)
            raise RuntimeError(f"Claw dashboard error: {response.reason_phrase or response.status_code}")
        return None
    try:
        body = response.json()
    except ValueError as exc:
        logger.error("Claw Dashboard 返回非 JSON：status=%s", response.status_code)
        raise RuntimeError(f"Claw dashboard returned non-JSON response: HTTP {response.status_code}") from exc
    if not response.is_success or body.get("success") is not True or body.get("code") != 200:
        logger.error(
            "Claw Dashboard 业务响应失败：status=%s code=%s message=%s",
            response.status_code,
            body.get("code") if isinstance(body, dict) else None,
            body.get("message") if isinstance(body, dict) else None,
        )
        raise RuntimeError(f"Claw dashboard error: {dashboard_error_message(str(body.get('message') or response.reason_phrase))}")
    return body.get("result")


def extract_auth_url(command: str | None) -> str | None:
    """从 OpenClaw 安装命令中提取 auth-url。"""

    if not command:
        return None
    match = re.search(r'--auth-url\s+"([^"]+)"', command)
    return match.group(1) if match else None


def optional_number(value: Any) -> int | float | None:
    """将 Dashboard 返回的数字字段安全转换为 int/float。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value)
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def normalize_mailbox(raw: dict[str, Any]) -> dict[str, Any]:
    """将 Claw Dashboard 邮箱对象转换为本地统一字段。"""

    email = str(raw.get("email") or "")
    install_command = raw.get("installCommand")
    return {
        "id": str(raw.get("id")),
        "email": email,
        "prefix": str(raw.get("prefix") or email.split("@")[0] or ""),
        "display_name": raw.get("displayName"),
        "mailbox_type": raw.get("mailboxType"),
        "status": raw.get("status"),
        "openclaw_status": raw.get("openclawStatus"),
        "install_command": install_command,
        "auth_url": extract_auth_url(install_command),
        "comm_level": optional_number(raw.get("commLevel")),
        "ext_receive_type": optional_number(raw.get("extReceiveType")),
        "ext_send_type": optional_number(raw.get("extSendType")),
        "created_at": raw.get("createdAt"),
    }


class ClawDashboardClient:
    """Claw Dashboard HTTP 客户端，复刻旧 Node 后端的内部接口调用。"""

    def __init__(
        self,
        repository: ClawRepository = claw_repository,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        """初始化 Claw Dashboard 客户端。"""

        self.repository = repository
        self.client_factory = client_factory

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """执行 Claw Dashboard HTTP 请求。

        参数:
            method: HTTP 方法。
            url: 请求地址。
            **kwargs: 传给 httpx.Client.request 的其他参数。

        返回:
            httpx 原始响应。

        异常:
            RuntimeError: 底层网络请求失败。
        """

        try:
            logger.debug("请求 Claw Dashboard：method=%s url=%s", method, url)
            with self.client_factory(timeout=30, trust_env=False) as client:
                response = client.request(method, url, **kwargs)
            logger.debug("Claw Dashboard 响应：method=%s url=%s status=%s", method, url, response.status_code)
            return response
        except Exception as exc:
            logger.error("Claw Dashboard 请求异常：method=%s url=%s error=%s", method, url, exc)
            raise RuntimeError(f"Claw dashboard request failed: {exc}") from exc

    def dashboard_cookie(self, connection_id: str | None = None) -> str:
        """读取指定连接的 Dashboard Cookie。"""

        connection = self.repository.resolve_connection(connection_id)
        cookie = connection.get("dashboard_cookie") if connection else None
        if not cookie:
            raise ValueError("CLAW_DASHBOARD_COOKIE is required for mailbox management; connect Claw first")
        return cookie

    def workspace_id(self, connection_id: str | None = None) -> str:
        """读取指定连接的 Claw workspace ID。"""

        connection = self.repository.resolve_connection(connection_id)
        workspace_id = connection.get("workspace_id") if connection else None
        if not workspace_id:
            raise ValueError("Claw workspace is not configured; connect Claw first")
        return workspace_id

    def parent_mailbox_id(self, connection_id: str | None = None) -> str:
        """读取指定连接的父邮箱 ID。"""

        connection = self.repository.resolve_connection(connection_id)
        parent_mailbox_id = connection.get("parent_mailbox_id") if connection else None
        if not parent_mailbox_id:
            raise ValueError("Claw parent mailbox is not configured; connect Claw first")
        return parent_mailbox_id

    def auth_headers(self, cookie: str | None = None, connection_id: str | None = None) -> dict[str, str]:
        """构造 Claw Dashboard 鉴权请求头。"""

        return {
            "accept": "application/json, text/plain, */*",
            "cookie": cookie or self.dashboard_cookie(connection_id),
        }

    def json_headers(self, connection_id: str | None = None) -> dict[str, str]:
        """构造 Claw Dashboard JSON 请求头。"""

        return {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "cookie": self.dashboard_cookie(connection_id),
        }

    def send_login_code(self, email: str) -> str:
        """发送 Claw 登录验证码。

        参数:
            email: 163 登录邮箱或邮箱前缀。

        返回:
            登录流程临时 Cookie。
        """

        normalized = validate_login_email(email)
        logger.info("发送 Claw 登录验证码：email=%s", normalized)
        response = self.request(
            "POST",
            f"{PUBLIC_BASE_URL}/auth/email/send-code",
            headers={
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "referer": f"{DASHBOARD_ORIGIN}/projects/dashboard/",
            },
            json={"email": normalized},
        )
        parse_dashboard_response(response)
        logger.info("Claw 登录验证码发送成功：email=%s status=%s", normalized, response.status_code)
        return read_set_cookie(response)

    def verify_login_code(self, email: str, code: str, pending_cookie: str | None = None) -> str:
        """校验 Claw 登录验证码并返回 Dashboard Cookie。

        参数:
            email: 163 登录邮箱或邮箱前缀。
            code: 用户收到的验证码。
            pending_cookie: 发送验证码阶段返回的临时 Cookie。

        返回:
            可用于 Dashboard 接口的 Cookie。
        """

        normalized = validate_login_email(email)
        logger.info("校验 Claw 登录验证码：email=%s hasPendingCookie=%s", normalized, bool(pending_cookie))
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "referer": f"{DASHBOARD_ORIGIN}/projects/dashboard/",
        }
        if pending_cookie:
            headers["cookie"] = pending_cookie
        response = self.request(
            "POST",
            f"{PUBLIC_BASE_URL}/auth/email/verify-code",
            headers=headers,
            json={"email": normalized, "code": code.strip()},
        )
        parse_dashboard_response(response)
        cookie = merge_cookie_headers([pending_cookie, read_set_cookie(response)])
        if not cookie:
            logger.error("Claw 验证码校验成功但未返回 Cookie：email=%s", normalized)
            raise RuntimeError("Claw login did not return a session cookie")
        logger.info("Claw 登录验证码校验成功：email=%s status=%s", normalized, response.status_code)
        return cookie

    def get_auth_me(self, cookie: str | None = None, connection_id: str | None = None) -> dict[str, Any] | None:
        """读取当前 Dashboard 登录用户信息。"""

        response = self.request("GET", f"{BASE_URL}/auth/me", headers=self.auth_headers(cookie, connection_id))
        return parse_dashboard_response(response)

    def list_workspaces(self, cookie: str | None = None, connection_id: str | None = None) -> list[dict[str, Any]]:
        """列出当前 Dashboard 账号可访问的 workspace。"""

        response = self.request("GET", f"{BASE_URL}/workspaces", headers=self.auth_headers(cookie, connection_id))
        result = parse_dashboard_response(response)
        workspaces = result.get("workspaces", []) if isinstance(result, dict) else []
        logger.info("获取 Claw workspace 列表成功：count=%s", len(workspaces))
        return workspaces

    def list_api_keys(self, cookie: str | None = None, connection_id: str | None = None) -> list[dict[str, Any]]:
        """列出当前 Dashboard 账号可用 API Key。"""

        response = self.request("GET", f"{BASE_URL}/api-keys", headers=self.auth_headers(cookie, connection_id))
        result = parse_dashboard_response(response)
        if isinstance(result, dict):
            candidates = result.get("apiKeys") or result.get("items") or []
        else:
            candidates = result if isinstance(result, list) else []
        api_keys = [item for item in candidates if isinstance(item, dict) and isinstance(item.get("apiKey"), str)]
        logger.info("获取 Claw API Key 列表成功：count=%s", len(api_keys))
        return api_keys

    def list_mailboxes(self, cookie: str | None = None, workspace_id: str | None = None, connection_id: str | None = None) -> list[dict[str, Any]]:
        """列出指定 workspace 下的主邮箱和子邮箱。"""

        resolved_workspace_id = workspace_id or self.workspace_id(connection_id)
        logger.info("获取 Claw 邮箱列表：workspaceId=%s connection=%s", resolved_workspace_id, connection_id or "legacy")
        response = self.request(
            "GET",
            f"{BASE_URL}/mailboxes?workspaceId={resolved_workspace_id}",
            headers={
                "accept": "application/json, text/plain, */*",
                "cookie": cookie or self.dashboard_cookie(connection_id),
            },
        )
        result = parse_dashboard_response(response)
        if isinstance(result, dict) and result.get("mailbox"):
            primary = normalize_mailbox(result["mailbox"])
            children = [
                normalize_mailbox(item)
                for item in result["mailbox"].get("subMailboxes", [])
                if isinstance(item, dict)
            ]
            mailboxes = [primary, *children]
            logger.info("获取 Claw 邮箱列表成功：workspaceId=%s count=%s", resolved_workspace_id, len(mailboxes))
            return mailboxes
        if isinstance(result, dict):
            candidates = result.get("items") or result.get("list") or result.get("mailboxes") or []
        else:
            candidates = result if isinstance(result, list) else []
        mailboxes = [normalize_mailbox(item) for item in candidates if isinstance(item, dict)]
        logger.info("获取 Claw 邮箱列表成功：workspaceId=%s count=%s", resolved_workspace_id, len(mailboxes))
        return mailboxes

    def create_mailbox(self, suffix: str, connection_id: str | None = None) -> dict[str, Any]:
        """在 Claw Dashboard 创建子邮箱。"""

        normalized = suffix.strip().lower()
        if not re.fullmatch(r"[a-z0-9]{1,32}", normalized):
            raise ValueError("suffix must contain 1-32 lowercase letters or digits")
        logger.info("创建 Claw 子邮箱：suffix=%s connection=%s", normalized, connection_id or "legacy")
        response = self.request(
            "POST",
            f"{BASE_URL}/mailboxes",
            headers=self.json_headers(connection_id),
            json={
                "prefix": normalized,
                "displayName": normalized,
                "mailboxType": "sub",
                "workspaceId": self.workspace_id(connection_id),
                "parentMailboxId": self.parent_mailbox_id(connection_id),
            },
        )
        mailbox = normalize_mailbox(parse_dashboard_response(response))
        logger.info("Claw 子邮箱创建成功：mailboxId=%s email=%s", mailbox["id"], mailbox["email"])
        return mailbox

    def update_mailbox_comm_settings(self, mailbox_id: str, payload: dict[str, Any], connection_id: str | None = None) -> None:
        """更新 Claw 邮箱通信设置。"""

        logger.info(
            "更新 Claw 邮箱通信设置：mailboxId=%s connection=%s commLevel=%s",
            mailbox_id,
            connection_id or "legacy",
            payload.get("commLevel"),
        )
        response = self.request(
            "POST",
            f"{BASE_URL}/mailboxes/comm-settings?id={mailbox_id}",
            headers=self.json_headers(connection_id),
            json=payload,
        )
        parse_dashboard_response(response)
        logger.info("Claw 邮箱通信设置更新成功：mailboxId=%s", mailbox_id)

    def delete_mailbox(self, mailbox_id: str, connection_id: str | None = None) -> None:
        """删除 Claw Dashboard 子邮箱。"""

        logger.info("删除 Claw 邮箱：mailboxId=%s connection=%s", mailbox_id, connection_id or "legacy")
        response = self.request(
            "POST",
            f"{BASE_URL}/mailboxes/delete?id={mailbox_id}",
            headers={
                "accept": "application/json, text/plain, */*",
                "cookie": self.dashboard_cookie(connection_id),
            },
        )
        parse_dashboard_response(response)
        logger.info("Claw 邮箱删除成功：mailboxId=%s", mailbox_id)


claw_dashboard_client = ClawDashboardClient()
