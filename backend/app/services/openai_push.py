import json
import logging
import threading
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.db.duck_repository import DuckRepository, duck_repository
from app.db.mail_repository import MailRepository, mail_repository
from app.services.openai_auth import (
    OpenAiAuthService,
    OpenAiLoginResult,
    contains_phone_requirement,
    openai_auth_service,
)
from app.services.sub2 import (
    Sub2AuthBranchFallbackError,
    Sub2AuthLoginCallback,
    Sub2AuthLoginRequest,
    convert_openai_oauth_to_sub2,
    is_sub2_auth_branch_fallback_error,
    sub2_service,
)
from app.services.telegram import TelegramService, telegram_service


logger = logging.getLogger(__name__)
OPENAI_DUCK_PUSH_JOB_TTL_SECONDS = 10 * 60
OPENAI_DUCK_PUSH_JOB_MAX_ITEMS = 200


def detect_openai_authorization_state(token: dict[str, Any]) -> str:
    """从已保存的 OpenAI 授权数据中提取可写入 Sub2 备注的登录状态。"""

    serialized = json.dumps(token, ensure_ascii=False).lower()
    if "add-phone" in serialized or "add_phone" in serialized:
        return "出现 add-phone"
    if token.get("accessToken") or token.get("access_token"):
        return "授权登录成功"
    return "授权状态未知"


def build_sub2_push_notes(address: dict[str, Any], token: dict[str, Any], push_mode: str) -> str:
    """生成推送到 Sub2 账号备注字段的状态说明。"""

    items = [
        f"ClawEmail：{detect_openai_authorization_state(token)}",
        f"推送模式：{push_mode}",
        f"Duck 邮箱：{address['address']}",
    ]
    forwarding_mailbox = address.get("forwarding_mailbox_email")
    if forwarding_mailbox:
        items.append(f"接收邮箱：{forwarding_mailbox}")
    return "；".join(items)


def format_openai_access_token_message(token: dict[str, Any]) -> str:
    """生成 Telegram 推送的 OpenAI access_token 消息。"""

    return "\n".join([
        f"OpenAI 账号推送成功：{token.get('email') or ''}",
        "",
        "access_token:",
        str(token.get("accessToken") or token.get("access_token") or ""),
    ])


def attach_sub2_account_notes(data: dict[str, Any], notes: str) -> dict[str, Any]:
    """给 Sub2 导入数据中的账号附加备注，保留可能已有的备注内容。"""

    accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else []
    return {
        **data,
        "accounts": [
            {
                **account,
                "notes": "\n".join(
                    item for item in [str(account.get("notes") or "").strip(), notes] if item
                ),
            }
            for account in accounts
            if isinstance(account, dict)
        ],
    }


class OpenAiDuckPushBody(BaseModel):
    """Duck 地址推送到 Sub2 的请求体。"""

    duck_address_id: int = Field(alias="duckAddressId", gt=0)
    group_id: int | None = Field(default=None, alias="groupId", gt=0)


class OpenAiPushService:
    """OpenAI Duck 地址推送服务，负责自动登录、落库和推送 Sub2。"""

    def __init__(
        self,
        repository: DuckRepository = duck_repository,
        mail_repository_value: MailRepository = mail_repository,
        auth_service: OpenAiAuthService = openai_auth_service,
        telegram_service_value: TelegramService = telegram_service,
    ) -> None:
        """初始化 OpenAI 推送服务。"""

        self.repository = repository
        self.mail_repository = mail_repository_value
        self.auth_service = auth_service
        self.telegram_service = telegram_service_value

    def resolve_duck_login_target(self, duck_address_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        """解析 Duck 地址和绑定的 Claw 收件箱。"""

        address = self.require_active_duck_address(duck_address_id)
        forwarding_email = str(address.get("forwarding_mailbox_email") or "").strip().lower()
        if not forwarding_email:
            logger.warning("Duck 地址推送失败，未绑定目标 Claw 邮箱：duckAddressId=%s", duck_address_id)
            raise RuntimeError("Duck 邮箱没有绑定目标 Claw 邮箱，无法读取 OpenAI 验证码")
        mailbox = self.mail_repository.get_mailbox_by_email(forwarding_email)
        if not mailbox or mailbox.get("status") == "deleted":
            logger.warning("Duck 地址推送失败，绑定 Claw 邮箱不存在：duckAddressId=%s mailbox=%s", duck_address_id, forwarding_email)
            raise RuntimeError("Duck 邮箱绑定的目标 Claw 邮箱不存在或已删除")
        # 提前读取 API Key，尽早暴露 Claw 连接配置问题。
        self.auth_service.mail_client.api_key(mailbox.get("connection_id"))
        return address, mailbox

    def require_active_duck_address(self, duck_address_id: int) -> dict[str, Any]:
        """读取并校验 Duck 地址必须存在且可用。"""

        address = self.repository.get_address(duck_address_id)
        if not address or address["status"] != "active":
            logger.warning("Duck 地址推送失败，记录不存在或不可用：duckAddressId=%s", duck_address_id)
            raise ValueError("Duck 邮箱记录不存在或已不可用")
        return address

    def load_saved_token(self, address: dict[str, Any]) -> dict[str, Any] | None:
        """读取 Duck 地址上已保存的 OpenAI OAuth JSON。"""

        raw_auth_json = address.get("openai_auth_json")
        if not raw_auth_json:
            return None
        try:
            token = json.loads(raw_auth_json)
        except ValueError as exc:
            logger.warning("Duck 地址推送失败，OpenAI OAuth JSON 非法：duckAddressId=%s", address.get("id"))
            raise ValueError("该 Duck 邮箱保存的 OpenAI 授权信息不是合法 JSON") from exc
        if not isinstance(token, dict):
            logger.warning("Duck 地址推送失败，OpenAI OAuth JSON 不是对象：duckAddressId=%s", address.get("id"))
            raise ValueError("该 Duck 邮箱保存的 OpenAI 授权信息必须是 JSON 对象")
        token.setdefault("email", address["address"])
        return token

    def notify_openai_access_token(self, token: dict[str, Any]) -> dict[str, Any]:
        """发送 OpenAI access_token Telegram 通知，失败时不影响推送结果。"""

        try:
            self.telegram_service.send_message(format_openai_access_token_message(token))
            return {"sent": True}
        except Exception as exc:
            logger.warning("OpenAI access_token Telegram 通知失败：email=%s error=%s", token.get("email"), exc)
            return {"sent": False, "error": str(exc)}

    def authorize_sub2_auth_login_with_current_session(
        self,
        request: Sub2AuthLoginRequest,
        login: OpenAiLoginResult,
    ) -> Sub2AuthLoginCallback:
        """使用当前 OpenAI 会话完成 Sub2 授权登录。"""

        logger.info(
            "开始 Sub2 授权登录：sessionId=%s email=%s proxyId=%s",
            request.session_id,
            request.email,
            request.proxy_id,
        )
        try:
            callback = self.auth_service.extract_callback_params(
                login.client,
                request.auth_url,
                login.device_id,
                fallback_on_phone_step=True,
                log_prefix="sub2_auth_branch_oauth",
            )
        except RuntimeError as exc:
            if contains_phone_requirement(str(exc)):
                raise Sub2AuthBranchFallbackError(str(exc)) from exc
            raise
        if not callback:
            raise Sub2AuthBranchFallbackError("Sub2 授权登录未拿到 OAuth callback code")
        if contains_phone_requirement(callback):
            raise Sub2AuthBranchFallbackError("Sub2 授权登录遇到 add-phone 步骤")
        logger.info("Sub2 授权登录拿到 OAuth callback：sessionId=%s hasState=%s", request.session_id, bool(callback.get("state")))
        return Sub2AuthLoginCallback(
            code=callback["code"],
            state=callback.get("state", ""),
            scope=callback.get("scope", ""),
        )

    def push_prepared_openai_account_to_sub2(
        self,
        login: OpenAiLoginResult,
        data: dict[str, Any],
        group_id: int | None,
    ) -> dict[str, Any]:
        """优先使用 Sub2 授权登录推送，遇到手机号步骤时降级为 OAuth token 推送。"""

        settings = sub2_service.get_settings()
        if not settings.open_ai_auth_login_enabled:
            logger.info("Sub2 授权登录分支已关闭，改用 OAuth token 推送：email=%s", login.token.get("email"))
            fallback = sub2_service.push_data(data, group_id)
            return {**fallback, "pushMode": "oauth_token"}
        try:
            logger.info("开始 Sub2 授权登录分支推送：email=%s", login.token.get("email"))
            result = sub2_service.push_data_via_auth_login(
                data,
                group_id,
                lambda request: self.authorize_sub2_auth_login_with_current_session(request, login),
            )
            logger.info("Sub2 授权登录分支推送完成：email=%s", login.token.get("email"))
            return {**result, "pushMode": "sub2_auth"}
        except Exception as exc:
            if not is_sub2_auth_branch_fallback_error(exc):
                raise
            message = str(exc)
            logger.warning("Sub2 授权登录分支降级：email=%s reason=%s", login.token.get("email"), message)
            fallback = sub2_service.push_data(data, group_id)
            return {**fallback, "pushMode": "fallback_oauth_token", "fallbackReason": message}

    def push_duck_address_to_sub2(self, duck_address_id: int, group_id: int | None = None) -> dict[str, Any]:
        """将 Duck 地址登录 OpenAI 后推送到 Sub2。

        参数:
            duck_address_id: 本地 Duck 地址 ID。
            group_id: 可选 Sub2 分组 ID；为空时使用默认分组。

        返回:
            推送结果、推送模式、降级原因和目标邮箱。
        """

        logger.info("开始推送 Duck OpenAI 凭据到 Sub2：duckAddressId=%s groupId=%s", duck_address_id, group_id)
        address = self.require_active_duck_address(duck_address_id)
        saved_token = self.load_saved_token(address)
        login: OpenAiLoginResult | None = None
        if saved_token:
            token = saved_token
            push_mode = "oauth_token"
            logger.info("Duck 地址已保存 OpenAI OAuth JSON，使用 token 直接推送：duckAddressId=%s", duck_address_id)
        else:
            address, mailbox = self.resolve_duck_login_target(duck_address_id)
            logger.info("Duck 地址缺少 OpenAI OAuth JSON，开始自动登录：duckAddressId=%s address=%s", duck_address_id, address.get("address"))
            login = self.auth_service.login_with_email_otp(address, address["address"], mailbox)
            token = login.token
            push_mode = "sub2_auth"
        data = attach_sub2_account_notes(
            convert_openai_oauth_to_sub2(token),
            build_sub2_push_notes(address, token, push_mode),
        )
        if login:
            result = self.push_prepared_openai_account_to_sub2(login, data, group_id)
            push_mode = result["pushMode"]
        else:
            result = {**sub2_service.push_data(data, group_id), "pushMode": push_mode}
        push_email = token.get("email") or address["address"]
        self.repository.mark_sub2_pushed(duck_address_id, push_mode, str(push_email))
        logger.info("Duck OpenAI 凭据推送完成：duckAddressId=%s email=%s", duck_address_id, push_email)
        return {
            "email": push_email,
            "pushMode": push_mode,
            "telegram": self.notify_openai_access_token(token),
            **result,
        }


openai_push_service = OpenAiPushService()


class OpenAiDuckPushJobService:
    """OpenAI Duck 推送后台任务服务。"""

    def __init__(
        self,
        push_service: OpenAiPushService = openai_push_service,
        ttl_seconds: int = OPENAI_DUCK_PUSH_JOB_TTL_SECONDS,
        max_items: int = OPENAI_DUCK_PUSH_JOB_MAX_ITEMS,
    ) -> None:
        """初始化任务服务。"""

        self.push_service = push_service
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self, duck_address_id: int, group_id: int | None = None) -> dict[str, Any]:
        """启动 Duck 推送后台任务并立即返回任务状态。"""

        job_id = uuid.uuid4().hex
        now = time.time()
        job = {
            "jobId": job_id,
            "status": "running",
            "duckAddressId": duck_address_id,
            "groupId": group_id,
            "createdAt": now,
            "updatedAt": now,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._prune_locked(now)
            self._jobs[job_id] = job
            snapshot = dict(job)
        thread = threading.Thread(
            target=self._run,
            args=(job_id, duck_address_id, group_id),
            name=f"openai-duck-push-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return snapshot

    def get(self, job_id: str) -> dict[str, Any] | None:
        """读取后台任务状态快照。"""

        with self._lock:
            self._prune_locked(time.time())
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _run(self, job_id: str, duck_address_id: int, group_id: int | None) -> None:
        """执行后台推送并写回任务状态。"""

        try:
            result = self.push_service.push_duck_address_to_sub2(duck_address_id, group_id)
            self._finish(job_id, "succeeded", result={"success": True, **result})
        except Exception as exc:
            logger.exception("Duck OpenAI 凭据后台推送失败：jobId=%s duckAddressId=%s", job_id, duck_address_id)
            self._finish(job_id, "failed", error=str(exc))

    def _finish(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """更新后台任务终态。"""

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = status
            job["result"] = result
            job["error"] = error
            job["updatedAt"] = time.time()

    def _prune_locked(self, now: float) -> None:
        """清理过期或超量的任务记录，调用方必须已持有锁。"""

        expired_ids = [
            job_id
            for job_id, job in self._jobs.items()
            if job.get("status") != "running" and now - float(job.get("updatedAt") or now) > self.ttl_seconds
        ]
        for job_id in expired_ids:
            self._jobs.pop(job_id, None)
        if len(self._jobs) <= self.max_items:
            return
        removable = sorted(
            (
                (float(job.get("updatedAt") or 0), job_id)
                for job_id, job in self._jobs.items()
                if job.get("status") != "running"
            ),
        )
        for _updated_at, job_id in removable[: max(0, len(self._jobs) - self.max_items)]:
            self._jobs.pop(job_id, None)


openai_push_job_service = OpenAiDuckPushJobService()
