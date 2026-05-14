import json
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.duck_repository import DuckRepository, duck_repository
from app.services.network_settings import network_settings_service


logger = logging.getLogger(__name__)
DUCK_ADDRESS_ENDPOINT = "https://quack.duckduckgo.com/api/email/addresses"
DUCK_GENERATE_MAX_ATTEMPTS = 5


class DuckAccountCreate(BaseModel):
    """Duck 账号创建请求。"""

    label: str = Field(min_length=1, max_length=80)
    token: str = Field(min_length=12)

    @field_validator("label", "token")
    @classmethod
    def trim_string(cls, value: str) -> str:
        """去掉账号名称和 Token 首尾空白。"""

        return value.strip()


class DuckAccountTokenUpdate(BaseModel):
    """Duck 账号 Token 更新请求。"""

    token: str = Field(min_length=12)

    @field_validator("token")
    @classmethod
    def trim_token(cls, value: str) -> str:
        """去掉 Token 首尾空白。"""

        return value.strip()


class DuckAddressCreate(BaseModel):
    """Duck 私有地址生成请求。"""

    forwarding_mailbox_email: str | None = Field(default=None, alias="forwardingMailboxEmail")
    note: str | None = Field(default=None, max_length=300)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("forwarding_mailbox_email")
    @classmethod
    def normalize_forwarding_email(cls, value: str | None) -> str | None:
        """归一化可选转发邮箱地址。"""

        if value is None:
            return None
        trimmed = str(value).strip().lower()
        return normalize_optional_email(trimmed)

    @field_validator("note")
    @classmethod
    def trim_note(cls, value: str | None) -> str | None:
        """去掉备注首尾空白。"""

        return value.strip() if value is not None else None


class DuckAddressUpdate(BaseModel):
    """Duck 私有地址更新请求。"""

    forwarding_mailbox_email: str | None = Field(default=None, alias="forwardingMailboxEmail")
    note: str | None = Field(default=None, max_length=300)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("forwarding_mailbox_email")
    @classmethod
    def normalize_forwarding_email(cls, value: str | None) -> str | None:
        """归一化可选转发邮箱地址。"""

        if value is None:
            return None
        trimmed = str(value).strip().lower()
        return normalize_optional_email(trimmed)

    @field_validator("note")
    @classmethod
    def trim_note(cls, value: str | None) -> str | None:
        """去掉备注首尾空白。"""

        return value.strip() if value is not None else None


class OpenAiCredentialsUpdate(BaseModel):
    """保存到 Duck 地址上的 OpenAI 凭据更新请求。"""

    password: str | None = None
    auth_json: Any = Field(default=None, alias="authJson")

    model_config = ConfigDict(populate_by_name=True)


def normalize_duck_token(value: str) -> str:
    """移除用户粘贴 Token 时可能带上的 Bearer 前缀。"""

    return re.sub(r"^Bearer\s+", "", value.strip(), flags=re.IGNORECASE).strip()


def duck_authorization_header(token: str) -> str:
    """生成 DuckDuckGo Email Protection API 鉴权头。"""

    return f"Bearer {normalize_duck_token(token)}"


def normalize_optional_email(value: str) -> str | None:
    """归一化可选邮箱地址并校验基本格式。"""

    if not value:
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ValueError("邮箱地址格式无效")
    return value


def normalize_duck_address(value: str) -> dict[str, str]:
    """校验并标准化 DuckDuckGo 返回的私有地址。"""

    local_part = re.sub(r"@duck\.com$", "", value.strip().lower(), flags=re.IGNORECASE)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", local_part):
        raise ValueError("Duck API returned an invalid private address")
    return {
        "address": f"{local_part}@duck.com",
        "local_part": local_part,
    }


def mask_duck_token(row: dict[str, Any]) -> dict[str, Any]:
    """返回 Duck 账号公开视图，Token 只保留前后缀。"""

    token = str(row.get("token") or "").strip()
    return {
        "id": row["id"],
        "label": row["label"],
        "status": row["status"],
        "last_error": row.get("last_error"),
        "last_used_at": row.get("last_used_at"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "token_prefix": token[:8] if token else None,
        "token_suffix": token[-4:] if token else None,
    }


def public_duck_address(row: dict[str, Any]) -> dict[str, Any]:
    """返回 Duck 私有地址公开视图，隐藏 OpenAI 密码和授权原文。"""

    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "address": row["address"],
        "local_part": row["local_part"],
        "forwarding_mailbox_email": row.get("forwarding_mailbox_email"),
        "note": row.get("note"),
        "status": row["status"],
        "raw_json": row["raw_json"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "has_openai_password": bool(row.get("openai_password")),
        "has_openai_auth_json": bool(row.get("openai_auth_json")),
        "sub2_pushed_at": row.get("sub2_pushed_at"),
        "sub2_push_mode": row.get("sub2_push_mode"),
        "sub2_push_email": row.get("sub2_push_email"),
        "is_sub2_pushed": bool(row.get("sub2_pushed_at")),
    }


def normalize_optional_json(value: Any) -> str | None | object:
    """归一化可选 JSON 字段，支持清空、字符串 JSON 和对象。"""

    if value is _UNSET:
        return _UNSET
    if value is None:
        return None
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        return json.dumps(json.loads(trimmed), ensure_ascii=False, indent=2)
    return json.dumps(value, ensure_ascii=False, indent=2)


_UNSET = object()


class DuckService:
    """迁移版 DuckDuckGo Email Protection 本地账号与地址服务。"""

    def __init__(
        self,
        repository: DuckRepository = duck_repository,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        """初始化 Duck 服务。"""

        self.repository = repository
        self.client_factory = client_factory

    def list_accounts(self) -> list[dict[str, Any]]:
        """列出本地保存的 Duck 账号，隐藏完整 Token。"""

        return [mask_duck_token(row) for row in self.repository.list_accounts()]

    def create_account(self, body: DuckAccountCreate) -> dict[str, Any]:
        """创建本地 Duck 账号记录。

        参数:
            body: 账号标签和 Duck Token。

        返回:
            脱敏后的账号信息。
        """

        row = self.repository.create_account(
            account_id=f"duck:{uuid.uuid4()}",
            label=body.label,
            token=normalize_duck_token(body.token),
        )
        logger.info("创建 Duck 账号：accountId=%s label=%s", row["id"], row["label"])
        return mask_duck_token(row)

    def update_account_token(self, account_id: str, body: DuckAccountTokenUpdate) -> dict[str, Any] | None:
        """更新 Duck 账号 Token。

        参数:
            account_id: 本地 Duck 账号 ID。
            body: 新 Token。

        返回:
            更新后的脱敏账号；账号不存在时返回 None。
        """

        row = self.repository.update_account_token(account_id, normalize_duck_token(body.token))
        if row:
            logger.info("更新 Duck 账号 Token：accountId=%s", account_id)
        else:
            logger.warning("更新 Duck 账号 Token 失败，账号不存在：accountId=%s", account_id)
        return mask_duck_token(row) if row else None

    def delete_account(self, account_id: str) -> bool:
        """禁用 Duck 账号。

        参数:
            account_id: 本地 Duck 账号 ID。

        返回:
            是否找到并禁用账号。
        """

        deleted = self.repository.delete_account(account_id)
        logger.info("删除 Duck 账号：accountId=%s success=%s", account_id, deleted)
        return deleted

    def list_addresses(
        self,
        account_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """分页列出 Duck 私有地址。"""

        result = self.repository.list_addresses(
            account_id=account_id,
            limit=limit,
            offset=offset,
            keyword=keyword.strip() if keyword else None,
        )
        return {
            **result,
            "items": [public_duck_address(row) for row in result["items"]],
        }

    def get_openai_password(self, address_id: int) -> str | None:
        """读取指定 Duck 地址保存的 OpenAI 密码。"""

        row = self.repository.get_address(address_id)
        if not row or row["status"] != "active":
            return None
        return row.get("openai_password") or None

    def get_openai_auth_json(self, address_id: int) -> str | None:
        """读取指定 Duck 地址保存的 OpenAI OAuth JSON。"""

        row = self.repository.get_address(address_id)
        if not row or row["status"] != "active":
            return None
        return row.get("openai_auth_json") or None

    def update_openai_credentials(self, address_id: int, body: OpenAiCredentialsUpdate) -> dict[str, Any] | None:
        """更新 Duck 地址绑定的 OpenAI 密码或 OAuth JSON。

        参数:
            address_id: Duck 地址本地 ID。
            body: 可选密码和 OAuth JSON。

        返回:
            更新后的公开地址信息；地址不存在时返回 None。
        """

        password = _UNSET if "password" not in body.model_fields_set else body.password or None
        try:
            auth_json = _UNSET if "auth_json" not in body.model_fields_set else normalize_optional_json(body.auth_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("OpenAI 授权信息必须是合法 JSON") from exc
        payload: dict[str, Any] = {}
        if password is not _UNSET:
            payload["password"] = password
        if auth_json is not _UNSET:
            payload["auth_json"] = auth_json
        row = self.repository.update_openai_credentials(address_id, payload)
        logger.info(
            "更新 Duck 地址 OpenAI 凭据：addressId=%s hasPassword=%s hasAuthJson=%s found=%s",
            address_id,
            password is not _UNSET,
            auth_json is not _UNSET,
            bool(row),
        )
        return public_duck_address(row) if row else None

    def update_address(self, address_id: int, body: DuckAddressUpdate) -> dict[str, Any] | None:
        """更新 Duck 私有地址元信息。"""

        payload: dict[str, Any] = {}
        if "forwarding_mailbox_email" in body.model_fields_set:
            payload["forwarding_mailbox_email"] = body.forwarding_mailbox_email
        if "note" in body.model_fields_set:
            payload["note"] = body.note
        row = self.repository.update_address(address_id, payload)
        logger.info("更新 Duck 地址：addressId=%s found=%s", address_id, bool(row))
        return public_duck_address(row) if row else None

    def delete_address(self, address_id: int) -> bool:
        """禁用 Duck 私有地址。"""

        deleted = self.repository.delete_address(address_id)
        logger.info("删除 Duck 地址：addressId=%s success=%s", address_id, deleted)
        return deleted

    def generate_address(self, account_id: str, body: DuckAddressCreate) -> dict[str, Any] | None:
        """通过 DuckDuckGo API 生成并保存私有地址。

        参数:
            account_id: 本地 Duck 账号 ID。
            body: 可选转发邮箱和备注。

        返回:
            生成后的公开地址信息；账号不可用时返回 None。

        异常:
            RuntimeError: Duck API 连续返回重复地址或远端请求失败。
        """

        account = self.repository.get_account(account_id)
        if not account or account["status"] == "disabled" or not account.get("token"):
            logger.warning("生成 Duck 地址失败，账号不存在或不可用：accountId=%s", account_id)
            return None

        try:
            logger.info("开始生成 Duck 地址：accountId=%s", account_id)
            generated = self.request_duck_address(account["token"])
            duplicate = self.repository.get_address_by_address(generated["address"])
            for _attempt in range(2, DUCK_GENERATE_MAX_ATTEMPTS + 1):
                if not duplicate:
                    break
                logger.warning(
                    "Duck API 返回重复地址，准备重试：accountId=%s address=%s attempt=%s",
                    account_id,
                    generated["address"],
                    _attempt,
                )
                generated = self.request_duck_address(account["token"])
                duplicate = self.repository.get_address_by_address(generated["address"])
            if duplicate:
                raise RuntimeError(f"DuckDuckGo 连续返回已存在地址：{generated['address']}，请稍后重试或检查 Token 是否受限")

            row = self.repository.save_address({
                "account_id": account["id"],
                "address": generated["address"],
                "local_part": generated["local_part"],
                "forwarding_mailbox_email": body.forwarding_mailbox_email,
                "note": body.note,
                "raw_json": json.dumps(generated["raw"], ensure_ascii=False),
            })
            self.repository.mark_account_used(account["id"])
            logger.info("Duck 地址生成成功：accountId=%s address=%s", account_id, row["address"])
            return public_duck_address(row)
        except Exception as exc:
            self.repository.mark_account_error(account["id"], str(exc))
            logger.error("Duck 地址生成失败：accountId=%s error=%s", account_id, exc)
            raise

    def request_duck_address(self, token: str) -> dict[str, Any]:
        """调用 DuckDuckGo Email Protection API 生成私有地址。

        参数:
            token: DuckDuckGo Email Protection Token。

        返回:
            标准化后的地址、local_part 和原始响应。

        异常:
            RuntimeError: 网络失败、响应非 JSON、HTTP 失败或响应缺少地址。
        """

        network_settings = network_settings_service.get()
        timeout_seconds = network_settings.timeout_ms / 1000
        proxy = network_settings.proxy_url or None
        try:
            logger.info("请求 DuckDuckGo 地址接口：proxy=%s timeoutMs=%s", bool(proxy), network_settings.timeout_ms)
            with self.client_factory(timeout=timeout_seconds, proxy=proxy) as client:
                response = client.request(
                    "POST",
                    DUCK_ADDRESS_ENDPOINT,
                    headers={
                        "authorization": duck_authorization_header(token),
                        "accept": "application/json",
                    },
                )
        except Exception as exc:
            logger.error("DuckDuckGo 地址接口网络异常：proxy=%s error=%s", bool(network_settings.proxy_url), exc)
            raise RuntimeError(self.network_error_message(exc, network_settings.proxy_url)) from exc

        try:
            body = response.json() if response.text.strip() else None
        except ValueError as exc:
            logger.error("DuckDuckGo 地址接口返回非 JSON：status=%s", response.status_code)
            raise RuntimeError(f"Duck address API returned non-JSON response: HTTP {response.status_code}") from exc

        if not response.is_success:
            message = body.get("message") if isinstance(body, dict) else response.reason_phrase or f"HTTP {response.status_code}"
            logger.error("DuckDuckGo 地址接口失败：status=%s message=%s", response.status_code, message)
            raise RuntimeError(f"Duck address API error: {message}")

        raw_address = body.get("address") if isinstance(body, dict) else None
        if not isinstance(raw_address, str) or not raw_address.strip():
            logger.error("DuckDuckGo 地址接口缺少 address 字段：status=%s", response.status_code)
            raise RuntimeError("Duck address API response did not include address")
        normalized = normalize_duck_address(raw_address)
        logger.info("DuckDuckGo 地址接口成功：address=%s", normalized["address"])
        return {**normalized, "raw": body}

    @staticmethod
    def network_error_message(error: Exception, proxy_url: str | None = None) -> str:
        """将 DuckDuckGo 网络异常转换为面向用户的中文错误。"""

        message = str(error)
        proxy_hint = "请检查系统设置里的系统代理地址是否可从容器访问。" if proxy_url else "请检查容器网络是否能直连 DuckDuckGo，或在系统设置里配置系统代理。"
        if "Timeout" in message or "timed out" in message or "timeout" in message:
            return f"DuckDuckGo 连接超时：{proxy_hint}"
        return f"DuckDuckGo 网络请求失败：{message}。{proxy_hint}"


duck_service = DuckService()
