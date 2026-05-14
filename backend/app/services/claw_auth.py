import logging
from typing import Any

from app.db.claw_repository import LEGACY_CONNECTION_ID, ClawRepository, claw_repository
from app.db.mail_repository import MailRepository, mail_repository
from app.db.settings_repository import SettingsRepository, settings_repository
from app.services.claw_dashboard import ClawDashboardClient, claw_dashboard_client, validate_login_email


logger = logging.getLogger(__name__)
AUTH_SETTING_KEYS = [
    "claw.apiKey",
    "claw.dashboardCookie",
    "claw.userEmail",
    "claw.workspaceId",
    "claw.workspaceName",
    "claw.parentMailboxId",
    "claw.rootPrefix",
    "claw.domain",
]


def connection_to_auth_status(connection: dict[str, Any] | None) -> dict[str, Any]:
    """将 Claw 连接记录转换为前端鉴权状态。

    参数:
        connection: 本地连接记录；为空表示未连接。

    返回:
        不包含敏感 Cookie/API Key 原文的连接状态。
    """

    api_key = connection.get("api_key") if connection else None
    cookie = connection.get("dashboard_cookie") if connection else None
    workspace_id = connection.get("workspace_id") if connection and cookie else None
    parent_mailbox_id = connection.get("parent_mailbox_id") if connection and cookie else None
    root_prefix = connection.get("root_prefix") if connection and cookie else None
    domain = connection.get("domain") if connection and cookie else None
    status = connection.get("status") if connection else None
    return {
        "id": connection.get("id") if connection else None,
        "connected": bool(api_key and cookie and workspace_id and parent_mailbox_id and root_prefix and domain and status != "disconnected"),
        "hasApiKey": bool(api_key),
        "hasDashboardCookie": bool(cookie),
        "userEmail": connection.get("user_email") if connection else None,
        "workspaceId": workspace_id,
        "workspaceName": connection.get("workspace_name") if connection else None,
        "parentMailboxId": parent_mailbox_id,
        "rootPrefix": root_prefix,
        "domain": domain,
        "apiKeyPrefix": api_key[:10] if isinstance(api_key, str) and api_key else None,
        "apiKeySuffix": api_key[-4:] if isinstance(api_key, str) and api_key else None,
        "status": status,
        "label": connection.get("label") if connection else None,
    }


def email_domain(email: str) -> str:
    """从邮箱地址中提取域名，缺失 @ 时回退到 Claw 默认域。"""

    return email.split("@")[1] if "@" in email else "claw.163.com"


def mailbox_root_prefix(mailbox: dict[str, Any]) -> str:
    """根据主邮箱记录推导子邮箱根前缀。"""

    prefix = mailbox.get("prefix")
    email = mailbox.get("email") or ""
    if prefix:
        return str(prefix).split("@")[0].split(".")[0]
    return str(email).split("@")[0].split(".")[0]


def connection_id_from_identity(user_email: str | None, workspace_id: str) -> str:
    """根据用户邮箱和 workspace 生成稳定连接 ID。"""

    base = f"{user_email or 'claw'}:{workspace_id}".strip().lower()
    return "".join(char if char.isalnum() or char in "._:-" else "-" for char in base)[:96]


class ClawAuthService:
    """Claw 连接状态与验证码登录服务。"""

    def __init__(
        self,
        repository: ClawRepository = claw_repository,
        settings_repository: SettingsRepository = settings_repository,
        mail_repository: MailRepository = mail_repository,
        dashboard: ClawDashboardClient = claw_dashboard_client,
    ) -> None:
        """初始化 Claw 鉴权服务及其依赖仓储。"""

        self.repository = repository
        self.settings_repository = settings_repository
        self.mail_repository = mail_repository
        self.dashboard = dashboard
        self.pending_login_cookies: dict[str, str] = {}

    def status(self, connection_id: str | None = None) -> dict[str, Any]:
        """读取指定 Claw 连接的鉴权状态。"""

        return connection_to_auth_status(self.repository.resolve_connection(connection_id))

    def list_connections(self) -> list[dict[str, Any]]:
        """列出所有 Claw 连接状态，包括已断开的历史连接。"""

        return [
            connection_to_auth_status(connection)
            for connection in self.repository.list_connections(include_disconnected=True)
        ]

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        """读取单个 Claw 连接状态。"""

        connection = self.repository.get_connection(connection_id)
        return connection_to_auth_status(connection) if connection else None

    def send_code(self, email: str) -> None:
        """发送 Claw 登录验证码并缓存临时 Cookie。"""

        normalized = validate_login_email(email)
        logger.info("准备发送 Claw 登录验证码：email=%s", normalized)
        pending_cookie = self.dashboard.send_login_code(normalized)
        if pending_cookie:
            self.pending_login_cookies[normalized] = pending_cookie
            logger.debug("缓存 Claw 登录临时 Cookie：email=%s", normalized)

    def verify_code(self, email: str, code: str, connection_id: str | None = None) -> dict[str, Any]:
        """校验 Claw 登录验证码并创建或刷新连接。"""

        normalized = validate_login_email(email)
        logger.info("准备校验 Claw 登录验证码：email=%s connection=%s", normalized, connection_id or "auto")
        cookie = self.dashboard.verify_login_code(normalized, code, self.pending_login_cookies.get(normalized))
        self.pending_login_cookies.pop(normalized, None)
        return self.connect_with_cookie(cookie, connection_id)

    def refresh(self, connection_id: str | None = None) -> dict[str, Any]:
        """使用已有 Dashboard Cookie 刷新 Claw 连接信息。"""

        connection = self.repository.resolve_connection(connection_id)
        if not connection:
            logger.warning("刷新 Claw 连接失败，连接不存在：connection=%s", connection_id or "legacy")
            raise ValueError("CLAW_DASHBOARD_COOKIE is required for mailbox management; connect Claw first")
        logger.info("刷新 Claw 连接：connection=%s", connection.get("id") or connection_id or "legacy")
        return self.connect_with_cookie(connection["dashboard_cookie"], connection.get("id") or connection_id)

    def logout(self, connection_id: str | None = None) -> dict[str, Any]:
        """断开 Claw 连接并清理 legacy 兼容配置。"""

        target_id = connection_id or LEGACY_CONNECTION_ID
        self.repository.mark_disconnected(target_id)
        if target_id == LEGACY_CONNECTION_ID:
            self.settings_repository.delete_many(AUTH_SETTING_KEYS)
        logger.info("Claw 连接已断开：connection=%s", target_id)
        return self.status(connection_id)

    def connect_with_cookie(self, cookie: str, preferred_connection_id: str | None = None) -> dict[str, Any]:
        """使用 Dashboard Cookie 拉取 Claw 账号上下文并保存连接。

        参数:
            cookie: Claw Dashboard 登录 Cookie。
            preferred_connection_id: 指定连接 ID；为空时按账号和 workspace 自动生成。

        返回:
            连接状态和同步到本地的邮箱数量。

        异常:
            RuntimeError: 账号缺少 workspace、API Key 或邮箱。
        """

        logger.info("开始建立 Claw 连接：preferredConnection=%s", preferred_connection_id or "auto")
        user = self.dashboard.get_auth_me(cookie)
        workspaces = self.dashboard.list_workspaces(cookie)
        api_keys = self.dashboard.list_api_keys(cookie)
        workspace = next((item for item in workspaces if item.get("status") == "active"), None) or next(iter(workspaces), None)
        if not workspace:
            logger.error("Claw 连接失败：账号没有可用 workspace")
            raise RuntimeError("Claw account has no active workspace")
        api_key = (
            next((item for item in api_keys if item.get("status") == "active" and item.get("defaultFlag") == 1), None)
            or next((item for item in api_keys if item.get("status") == "active"), None)
            or next(iter(api_keys), None)
        )
        if not api_key or not api_key.get("apiKey"):
            logger.error("Claw 连接失败：workspace=%s 没有可用 API Key", workspace.get("id"))
            raise RuntimeError("Claw account has no API key to use")
        mailboxes = self.dashboard.list_mailboxes(cookie=cookie, workspace_id=workspace["id"])
        primary = (
            next((item for item in mailboxes if item.get("mailbox_type") == "primary"), None)
            or next((item for item in mailboxes if "." not in str(item.get("email") or "").split("@")[0]), None)
            or next(iter(mailboxes), None)
        )
        if not primary:
            logger.error("Claw 连接失败：workspace=%s 没有邮箱", workspace.get("id"))
            raise RuntimeError("Claw account has no mailbox")

        user_email = user.get("email") if isinstance(user, dict) and isinstance(user.get("email"), str) else None
        if user_email is None and isinstance(user, dict) and isinstance(user.get("emailAddress"), str):
            user_email = user["emailAddress"]
        connection_id = preferred_connection_id or connection_id_from_identity(user_email, workspace["id"])
        connection = self.repository.upsert_connection({
            "id": connection_id,
            "label": user_email or workspace.get("name") or connection_id,
            "user_email": user_email,
            "workspace_id": workspace["id"],
            "workspace_name": workspace.get("name"),
            "parent_mailbox_id": primary["id"],
            "root_prefix": mailbox_root_prefix(primary),
            "domain": email_domain(primary["email"]),
            "api_key": api_key["apiKey"],
            "dashboard_cookie": cookie,
            "status": "active",
        })
        logger.info(
            "Claw 连接保存成功：connection=%s userEmail=%s workspaceId=%s mailboxCount=%s",
            connection_id,
            user_email,
            workspace["id"],
            len(mailboxes),
        )
        # 连接成功后立即把 Dashboard 邮箱快照写入本地缓存，减少前端首次加载空白状态。
        for mailbox in mailboxes:
            self.mail_repository.upsert_mailbox({
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
        if connection_id == LEGACY_CONNECTION_ID:
            # 保留旧 Node 配置键，迁移期间让新旧后端可以共享同一套连接信息。
            self.settings_repository.set("claw.apiKey", api_key["apiKey"])
            self.settings_repository.set("claw.dashboardCookie", cookie)
            self.settings_repository.set("claw.workspaceId", workspace["id"])
            self.settings_repository.set("claw.parentMailboxId", primary["id"])
            self.settings_repository.set("claw.rootPrefix", mailbox_root_prefix(primary))
            self.settings_repository.set("claw.domain", email_domain(primary["email"]))
            if user_email:
                self.settings_repository.set("claw.userEmail", user_email)
            if workspace.get("name"):
                self.settings_repository.set("claw.workspaceName", workspace["name"])
        auth_status = connection_to_auth_status(connection)
        logger.info("Claw 连接状态刷新完成：connection=%s connected=%s", connection_id, auth_status["connected"])
        return {
            "connection": auth_status,
            "auth": auth_status,
            "syncedMailboxes": len(mailboxes),
        }


claw_auth_service = ClawAuthService()
