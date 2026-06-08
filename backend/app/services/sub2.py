import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_settings
from app.db.settings_repository import SettingsRepository, settings_repository
from app.services.network_settings import network_settings_service


logger = logging.getLogger(__name__)
API_URL_KEY = "sub2.apiUrl"
API_KEY_KEY = "sub2.apiKey"
DEFAULT_GROUP_ID_KEY = "sub2.defaultGroupId"
DEFAULT_PROXY_ID_KEY = "sub2.defaultProxyId"
OPENAI_AUTH_LOGIN_ENABLED_KEY = "sub2.openAiAuthLoginEnabled"


class Sub2Settings(BaseModel):
    """Sub2API 私有配置，包含敏感 API Key。"""

    api_url: str = Field(default="", alias="apiUrl")
    api_key: str = Field(default="", alias="apiKey")
    default_group_id: int | None = Field(default=None, alias="defaultGroupId")
    default_proxy_id: int | None = Field(default=None, alias="defaultProxyId")
    open_ai_auth_login_enabled: bool = Field(default=True, alias="openAiAuthLoginEnabled")

    model_config = ConfigDict(populate_by_name=True)


class Sub2PublicSettings(BaseModel):
    """返回给前端的 Sub2API 配置，敏感字段只暴露脱敏预览。"""

    api_url: str = Field(default="", alias="apiUrl")
    has_api_key: bool = Field(default=False, alias="hasApiKey")
    api_key_preview: str | None = Field(default=None, alias="apiKeyPreview")
    default_group_id: int | None = Field(default=None, alias="defaultGroupId")
    default_proxy_id: int | None = Field(default=None, alias="defaultProxyId")
    open_ai_auth_login_enabled: bool = Field(default=True, alias="openAiAuthLoginEnabled")

    model_config = ConfigDict(populate_by_name=True)


class Sub2SettingsUpdate(BaseModel):
    """Sub2API 配置更新请求。"""

    api_url: str | None = Field(default=None, alias="apiUrl", max_length=500)
    api_key: str | None = Field(default=None, alias="apiKey", max_length=500)
    default_group_id: int | None = Field(default=None, alias="defaultGroupId")
    default_proxy_id: int | None = Field(default=None, alias="defaultProxyId")
    open_ai_auth_login_enabled: bool | None = Field(default=None, alias="openAiAuthLoginEnabled")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("api_url", "api_key")
    @classmethod
    def trim_optional_string(cls, value: str | None) -> str | None:
        """去掉可选字符串配置首尾空白。"""

        if value is None:
            return None
        return value.strip()

    @field_validator("default_group_id")
    @classmethod
    def validate_group_id(cls, value: int | None) -> int | None:
        """校验 Sub2 默认分组 ID 必须为正整数。"""

        if value is None:
            return None
        if isinstance(value, bool) or value <= 0:
            raise ValueError("Sub2 默认分组 ID 无效")
        return value

    @field_validator("default_proxy_id")
    @classmethod
    def validate_proxy_id(cls, value: int | None) -> int | None:
        """校验 Sub2 默认代理 ID 必须为正整数。"""

        if value is None:
            return None
        if isinstance(value, bool) or value <= 0:
            raise ValueError("Sub2 默认代理 ID 无效")
        return value


class Sub2Group(BaseModel):
    """Sub2API 分组摘要。"""

    id: int
    name: str | None = None


class Sub2Proxy(BaseModel):
    """Sub2API 代理摘要。"""

    id: int
    name: str | None = None
    protocol: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None


class Sub2GroupsResponse(BaseModel):
    """Sub2API 分组列表响应。"""

    items: list[Sub2Group]


class Sub2ProxiesResponse(BaseModel):
    """Sub2API 代理列表响应。"""

    items: list[Sub2Proxy]


class Sub2AccountPayload(BaseModel):
    """Sub2 账号转换或推送请求体。"""

    input: Any
    group_id: int | None = Field(default=None, alias="groupId")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: int | None) -> int | None:
        """校验本次推送指定的分组 ID 必须为正整数。"""

        if value is None:
            return None
        if isinstance(value, bool) or value <= 0:
            raise ValueError("Sub2 默认分组 ID 无效")
        return value


class Sub2DataPayload(BaseModel):
    """Sub2 已转换导入数据推送请求体。"""

    data: dict[str, Any]
    group_id: int | None = Field(default=None, alias="groupId")
    proxy_id: int | None = Field(default=None, alias="proxyId")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: int | None) -> int | None:
        """校验本次推送指定的分组 ID 必须为正整数。"""

        if value is None:
            return None
        if isinstance(value, bool) or value <= 0:
            raise ValueError("Sub2 默认分组 ID 无效")
        return value

    @field_validator("proxy_id")
    @classmethod
    def validate_proxy_id(cls, value: int | None) -> int | None:
        """校验本次推送指定的代理 ID 必须为正整数。"""

        if value is None:
            return None
        if isinstance(value, bool) or value <= 0:
            raise ValueError("Sub2 代理 ID 无效")
        return value

    @field_validator("data")
    @classmethod
    def validate_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        """校验已转换数据必须包含账号列表。"""

        accounts = value.get("accounts")
        if not isinstance(accounts, list) or not accounts:
            raise ValueError("待推送数据必须包含 accounts 数组")
        return value


class Sub2ConvertResponse(BaseModel):
    """Sub2 账号转换响应。"""

    data: dict[str, Any]


class Sub2PushResponse(BaseModel):
    """Sub2 账号推送响应。"""

    success: bool = True
    data: dict[str, Any]
    response: Any | None = None


@dataclass(slots=True)
class Sub2AuthLoginRequest:
    """Sub2 OpenAI 授权登录请求。"""

    auth_url: str
    session_id: str
    email: str
    account: dict[str, Any]
    proxy_id: int | None


@dataclass(slots=True)
class Sub2AuthLoginCallback:
    """Sub2 OpenAI 授权登录回调参数。"""

    code: str
    state: str
    scope: str = ""


class Sub2AuthBranchFallbackError(RuntimeError):
    """Sub2 授权登录遇到手机号步骤时使用的降级异常。"""


def is_sub2_auth_branch_fallback_error(error: Any) -> bool:
    """判断异常是否需要从 Sub2 授权登录降级到 OAuth token 推送。"""

    return isinstance(error, Sub2AuthBranchFallbackError) or (
        re.search(r"add[-_ ]?phone|phone[_-]?verification|phone[_-]?number|绑定手机号|手机", str(error), re.I)
        is not None
    )


def trim_string(value: str | None) -> str:
    """归一化可空字符串配置。"""

    return (value or "").strip()


def mask_api_key(api_key: str) -> str | None:
    """生成 API Key 脱敏预览，避免前端或日志泄露完整密钥。"""

    if not api_key:
        return None
    if len(api_key) <= 12:
        return f"{api_key[:4]}****"
    return f"{api_key[:8]}...{api_key[-4:]}"


def parse_optional_positive_id(value: str | int | None) -> int | None:
    """从旧配置字符串中解析可选正整数 ID。"""

    trimmed = str(value).strip() if value is not None else ""
    if not trimmed:
        return None
    try:
        parsed = float(trimmed)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() and parsed > 0 else None


def parse_optional_group_id(value: str | int | None) -> int | None:
    """从旧配置字符串中解析可选 Sub2 分组 ID。"""

    return parse_optional_positive_id(value)


def parse_bool_setting(value: str | None, default: bool) -> bool:
    """解析 SQLite 中保存的布尔配置，缺失或非法时使用默认值。"""

    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _replace_path(parsed, path: str):
    """替换 URL path 并清空 query/fragment。"""

    return parsed._replace(path=path, params="", query="", fragment="")


def normalize_sub2_import_url(api_url: str) -> str:
    """将用户输入的 Sub2API 地址规范化为账号导入接口地址。

    参数:
        api_url: 用户配置的 Sub2API 根地址或导入接口地址。

    返回:
        /api/v1/admin/accounts/data 接口完整地址。

    异常:
        ValueError: 地址为空或不是合法 HTTP(S) URL。
    """

    trimmed = api_url.strip()
    if not trimmed:
        raise ValueError("请先在系统设置里配置 Sub2API 地址")
    parsed = urlparse(trimmed)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Sub2API 地址必须是有效的 http:// 或 https:// 地址")
    path = parsed.path.rstrip("/")
    if path.endswith("/admin/accounts/data"):
        return urlunparse(_replace_path(parsed, parsed.path))
    prefix = path if path.endswith("/api/v1") else f"{path}/api/v1"
    normalized_path = f"{prefix}/admin/accounts/data".replace("//", "/")
    return urlunparse(_replace_path(parsed, normalized_path))


def normalize_sub2_groups_url(api_url: str) -> str:
    """生成 Sub2 OpenAI 可用分组查询地址。"""

    parsed = urlparse(normalize_sub2_import_url(api_url))
    path = parsed.path.removesuffix("/admin/accounts/data") + "/admin/groups"
    query = urlencode({
        "page": "1",
        "page_size": "1000",
        "platform": "openai",
        "status": "active",
    })
    return urlunparse(parsed._replace(path=path, params="", query=query, fragment=""))


def normalize_sub2_accounts_url(api_url: str) -> str:
    """生成 Sub2 账号创建接口地址。"""

    parsed = urlparse(normalize_sub2_import_url(api_url))
    path = parsed.path.removesuffix("/admin/accounts/data") + "/admin/accounts"
    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))


def normalize_sub2_proxies_url(api_url: str) -> str:
    """生成 Sub2 代理创建接口地址。"""

    parsed = urlparse(normalize_sub2_import_url(api_url))
    path = parsed.path.removesuffix("/admin/accounts/data") + "/admin/proxies"
    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))


def normalize_sub2_openai_auth_url(api_url: str, action: str) -> str:
    """生成 Sub2 OpenAI 授权登录接口地址。

    参数:
        api_url: 用户配置的 Sub2API 地址。
        action: 授权登录动作，仅允许 generate-auth-url 或 create-from-oauth。

    返回:
        Sub2 OpenAI 授权登录接口完整地址。

    异常:
        ValueError: 动作名称不在白名单内。
    """

    if action not in {"generate-auth-url", "create-from-oauth"}:
        raise ValueError("Sub2 OpenAI 授权登录动作无效")
    parsed = urlparse(normalize_sub2_import_url(api_url))
    path = parsed.path.removesuffix("/admin/accounts/data") + f"/admin/openai/{action}"
    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))


def normalize_sub2_proxy_list_url(api_url: str, proxy: dict[str, Any]) -> str:
    """生成按代理端点搜索 Sub2 代理的列表接口地址。"""

    parsed = urlparse(normalize_sub2_proxies_url(api_url))
    query = urlencode({
        "page": "1",
        "page_size": "20",
        "protocol": str(proxy.get("protocol") or ""),
        "status": "active",
        "search": str(proxy.get("host") or ""),
        "sort_by": "id",
        "sort_order": "desc",
    })
    return urlunparse(parsed._replace(query=query))


def normalize_sub2_default_proxy_list_url(api_url: str) -> str:
    """生成获取 Sub2 最新可用代理的列表接口地址。"""

    parsed = urlparse(normalize_sub2_proxies_url(api_url))
    query = urlencode({
        "page": "1",
        "page_size": "1",
        "status": "active",
        "sort_by": "id",
        "sort_order": "desc",
    })
    return urlunparse(parsed._replace(query=query))


def normalize_sub2_active_proxies_url(api_url: str) -> str:
    """生成获取 Sub2 可用代理列表的接口地址。"""

    parsed = urlparse(normalize_sub2_proxies_url(api_url))
    query = urlencode({
        "page": "1",
        "page_size": "1000",
        "status": "active",
        "sort_by": "id",
        "sort_order": "asc",
    })
    return urlunparse(parsed._replace(query=query))


def sub2_auth_headers(api_key: str) -> dict[str, str]:
    """根据 API Key 格式生成 Sub2API 鉴权头。"""

    trimmed = api_key.strip()
    if trimmed.lower().startswith("bearer "):
        return {"authorization": trimmed}
    return {"x-api-key": trimmed}


def number_field(record: dict[str, Any], key: str) -> int | None:
    """从 Sub2 返回对象中安全读取整数字段。"""

    value = record.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value)
        except ValueError:
            return None
        if parsed.is_integer():
            return int(parsed)
    return None


def extract_sub2_groups(body: Any) -> list[Sub2Group]:
    """兼容不同 Sub2API 响应包裹格式并提取分组列表。"""

    candidates: list[Any] = []
    if isinstance(body, list):
        candidates.extend(body)
    elif isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            candidates.extend(data)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            candidates.extend(data["items"])
        if isinstance(body.get("items"), list):
            candidates.extend(body["items"])

    groups: list[Sub2Group] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        group_id = number_field(item, "id")
        if group_id is None:
            continue
        name = item.get("name")
        groups.append(Sub2Group(id=group_id, name=name if isinstance(name, str) else None))
    return groups


def extract_list_items(body: Any) -> list[Any]:
    """从常见列表响应结构中提取 items/data 数组。"""

    candidates: list[Any] = []
    if isinstance(body, list):
        candidates.extend(body)
    elif isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            candidates.extend(data)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            candidates.extend(data["items"])
        if isinstance(body.get("items"), list):
            candidates.extend(body["items"])
    return candidates


def extract_sub2_proxies(body: Any) -> list[dict[str, Any]]:
    """从 Sub2API 响应中提取可用于账号绑定的代理摘要。"""

    proxies: list[dict[str, Any]] = []
    for item in extract_list_items(body):
        if not isinstance(item, dict):
            continue
        proxy_id = number_field(item, "id")
        if proxy_id is None:
            continue
        proxies.append({
            "id": proxy_id,
            "name": item.get("name") if isinstance(item.get("name"), str) else None,
            "protocol": item.get("protocol") if isinstance(item.get("protocol"), str) else None,
            "host": item.get("host") if isinstance(item.get("host"), str) else None,
            "port": number_field(item, "port"),
            "username": item.get("username") if isinstance(item.get("username"), str) else None,
            "password": item.get("password") if isinstance(item.get("password"), str) else None,
        })
    return proxies


def sub2_error_message(body: Any, fallback: str) -> str:
    """从 Sub2API 错误响应中提取最有用的错误信息。"""

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"]:
            return error["message"]
        for key in ("message", "error", "detail", "reason"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


def parse_json_or_text(text: str) -> Any:
    """将响应文本解析为 JSON；非 JSON 文本包装成 message 对象。"""

    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return {"message": text}


def unwrap_sub2_data(body: Any) -> Any:
    """解开 Sub2API 常见的 data 包裹。"""

    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def extract_sub2_auth_session(body: Any) -> dict[str, str]:
    """从 Sub2 授权登录响应中提取 auth_url 和 session_id。

    参数:
        body: Sub2 generate-auth-url 接口响应体。

    返回:
        包含 auth_url 和 session_id 的字典。

    异常:
        RuntimeError: 响应结构无效或缺少必要字段。
    """

    data = unwrap_sub2_data(body)
    if not isinstance(data, dict):
        raise RuntimeError("Sub2API 授权登录响应格式无效")
    auth_url = string_field(data, "auth_url")
    session_id = string_field(data, "session_id")
    if not auth_url or not session_id:
        raise RuntimeError("Sub2API 授权登录响应缺少 auth_url 或 session_id")
    return {"auth_url": auth_url, "session_id": session_id}


def auth_url_state(auth_url: str) -> str:
    """从 Sub2 授权 URL 中提取 state 参数。"""

    try:
        parsed = urlparse(auth_url)
        return dict(parse_qsl(parsed.query)).get("state", "").strip()
    except Exception:
        return ""


def as_record(value: Any, field: str) -> dict[str, Any]:
    """校验输入字段必须是 JSON 对象。"""

    if not isinstance(value, dict):
        raise ValueError(f"输入 JSON 缺少对象字段：{field}")
    return value


def string_field(record: dict[str, Any], key: str) -> str:
    """从对象中读取字符串字段并裁剪空白。"""

    value = record.get(key)
    return value.strip() if isinstance(value, str) else ""


def normalize_expires_at(value: str) -> str:
    """将 ChatGPT/OpenAI 过期时间转换为 Sub2 需要的 +08:00 格式。"""

    try:
        normalized = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("输入 JSON 的 expires 不是有效时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(timezone.utc) + timedelta(hours=8)
    return f"{local.strftime('%Y-%m-%dT%H:%M:%S')}+08:00"


def exported_at() -> str:
    """生成 Sub2 导出数据的 UTC 时间戳。"""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_configured_proxies() -> list[dict[str, Any]]:
    """读取环境变量中的 Sub2 代理模板。

    说明:
        该模板只作为兼容旧配置的输入源。推送时如果缺少 proxy_key，
        会优先从 Sub2API 查询已有代理，而不是要求 temp JSON 固定带 proxy_key。
    """

    raw = get_settings().sub2_proxy_template_json.strip()
    if not raw:
        return []
    parsed = json.loads(raw)
    candidates = parsed if isinstance(parsed, list) else parsed.get("proxies") if isinstance(parsed, dict) else []
    return [item for item in candidates if isinstance(item, dict)] if isinstance(candidates, list) else []


def first_proxy_key(proxies: list[dict[str, Any]]) -> str:
    """读取代理模板中的第一个有效 proxy_key。"""

    for proxy in proxies:
        proxy_key = proxy.get("proxy_key")
        if isinstance(proxy_key, str) and proxy_key.strip():
            return proxy_key.strip()
    return ""


def convert_chat_gpt_session_to_sub2(input_value: Any) -> dict[str, Any]:
    """将旧 ChatGPT session JSON 转换为 Sub2 导入格式。

    参数:
        input_value: 旧 Node 版导出的 ChatGPT session JSON。

    返回:
        包含 accounts/proxies 的 Sub2 导入数据。

    异常:
        ValueError: 输入 JSON 缺少 OpenAI 账号、用户或 token 关键字段。
    """

    root = as_record(input_value, "root")
    user = as_record(root.get("user"), "user")
    account = as_record(root.get("account"), "account")
    email = string_field(user, "email")
    access_token = string_field(root, "accessToken")
    expires = string_field(root, "expires")
    account_id = string_field(account, "id")
    user_id = string_field(user, "id")
    plan_type = string_field(account, "planType") or "unknown"
    if not email:
        raise ValueError("输入 JSON 缺少 user.email")
    if not access_token:
        raise ValueError("输入 JSON 缺少 accessToken")
    if not expires:
        raise ValueError("输入 JSON 缺少 expires")
    if not account_id:
        raise ValueError("输入 JSON 缺少 account.id")
    if not user_id:
        raise ValueError("输入 JSON 缺少 user.id")

    proxies = read_configured_proxies()
    proxy_key = first_proxy_key(proxies)
    return {
        "exported_at": exported_at(),
        "proxies": proxies,
        "accounts": [{
            "name": email,
            "platform": "openai",
            "type": "oauth",
            "credentials": {
                "access_token": access_token,
                "chatgpt_account_id": account_id,
                "chatgpt_user_id": user_id,
                "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
                "email": email,
                "expires_at": normalize_expires_at(expires),
                "plan_type": plan_type,
            },
            "extra": {
                "email": email,
                "openai_oauth_responses_websockets_v2_enabled": False,
                "openai_oauth_responses_websockets_v2_mode": "off",
                "privacy_mode": "training_off",
            },
            "proxy_key": proxy_key,
            "concurrency": 10,
            "priority": 50,
            "rate_multiplier": 1,
            "auto_pause_on_expired": True,
        }],
    }


def normalize_openai_oauth_expires_at(value: str | None) -> str:
    """归一化 OpenAI OAuth 过期时间；空值保持为空。"""

    if not value:
        return ""
    return normalize_expires_at(value)


def convert_openai_oauth_to_sub2(input_value: dict[str, Any]) -> dict[str, Any]:
    """将 OpenAI OAuth 登录结果转换为 Sub2 导入格式。

    参数:
        input_value: 已保存的 OpenAI OAuth JSON。

    返回:
        包含单个 OpenAI OAuth 账号的 Sub2 导入数据。

    异常:
        ValueError: 缺少 email 或 access_token。
    """

    email = string_field(input_value, "email").lower()
    access_token = string_field(input_value, "accessToken") or string_field(input_value, "access_token")
    if not email:
        raise ValueError("OpenAI OAuth 结果缺少 email")
    if not access_token:
        raise ValueError("OpenAI OAuth 结果缺少 access_token")
    user_id = string_field(input_value, "userId") or string_field(input_value, "user_id") or email
    account_id = string_field(input_value, "accountId") or string_field(input_value, "account_id") or user_id
    expires_at = string_field(input_value, "expiresAt") or string_field(input_value, "expires_at")
    plan_type = string_field(input_value, "planType") or string_field(input_value, "plan_type") or "unknown"
    refresh_token = string_field(input_value, "refreshToken") or string_field(input_value, "refresh_token")
    id_token = string_field(input_value, "idToken") or string_field(input_value, "id_token")
    credentials = {
        "access_token": access_token,
        "chatgpt_account_id": account_id,
        "chatgpt_user_id": user_id,
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "email": email,
        "expires_at": normalize_openai_oauth_expires_at(expires_at),
        "plan_type": plan_type,
    }
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    if id_token:
        credentials["id_token"] = id_token
    proxies = read_configured_proxies()
    proxy_key = first_proxy_key(proxies)
    return {
        "exported_at": exported_at(),
        "proxies": proxies,
        "accounts": [{
            "name": email,
            "platform": "openai",
            "type": "oauth",
            "credentials": credentials,
            "extra": {
                "email": email,
                "openai_oauth_responses_websockets_v2_enabled": False,
                "openai_oauth_responses_websockets_v2_mode": "off",
                "privacy_mode": "training_off",
            },
            "proxy_key": proxy_key,
            "concurrency": 10,
            "priority": 50,
            "rate_multiplier": 1,
            "auto_pause_on_expired": True,
        }],
    }


def proxy_key_from_sub2_proxy(proxy: dict[str, Any]) -> str:
    """根据 Sub2 代理字段生成稳定 proxy_key。"""

    if proxy.get("protocol") and proxy.get("host") and proxy.get("port"):
        return "|".join([
            str(proxy.get("protocol") or ""),
            str(proxy.get("host") or ""),
            str(proxy.get("port") or ""),
            str(proxy.get("username") or ""),
            str(proxy.get("password") or ""),
        ])
    return f"sub2-proxy:{proxy.get('id')}"


def to_sub2_proxy(proxy: dict[str, Any]) -> dict[str, Any]:
    """将 Sub2API 代理记录转换为导入数据的代理结构。"""

    return {
        "id": proxy.get("id"),
        "proxy_key": proxy_key_from_sub2_proxy(proxy),
        "name": proxy.get("name") or proxy.get("host") or f"proxy-{proxy.get('id')}",
        "protocol": proxy.get("protocol"),
        "host": proxy.get("host"),
        "port": proxy.get("port"),
        "username": proxy.get("username"),
        "password": proxy.get("password"),
        "status": "active",
    }


def proxy_matches(candidate: dict[str, Any], proxy: dict[str, Any]) -> bool:
    """判断 Sub2 现有代理是否与导入数据中的代理等价。"""

    same_endpoint = (
        str(candidate.get("protocol") or "") == str(proxy.get("protocol") or "")
        and str(candidate.get("host") or "") == str(proxy.get("host") or "")
        and candidate.get("port") == number_field(proxy, "port")
        and str(candidate.get("username") or "") == str(proxy.get("username") or "")
    )
    if not same_endpoint:
        return False

    candidate_password = str(candidate.get("password") or "").strip()
    proxy_password = str(proxy.get("password") or "").strip()
    if not candidate_password or "*" in candidate_password:
        return True
    return candidate_password == proxy_password


def proxy_by_key(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """按 proxy_key 建立导入数据代理索引。"""

    proxies: dict[str, dict[str, Any]] = {}
    for proxy in data.get("proxies") if isinstance(data.get("proxies"), list) else []:
        if not isinstance(proxy, dict):
            continue
        proxy_key = proxy.get("proxy_key")
        if isinstance(proxy_key, str) and proxy_key.strip():
            proxies[proxy_key.strip()] = {**proxy, "proxy_key": proxy_key.strip()}
    return proxies


def apply_sub2_group(data: dict[str, Any], group_id: int) -> dict[str, Any]:
    """给导入数据中的所有账号附加 Sub2 分组 ID。"""

    accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else []
    return {
        **data,
        "accounts": [
            {**account, "group_ids": [group_id]} if isinstance(account, dict) else account
            for account in accounts
        ],
    }


class Sub2Service:
    """迁移版 Sub2API 配置、转换和推送服务。"""

    def __init__(
        self,
        repository: SettingsRepository = settings_repository,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        """初始化 Sub2 服务。"""

        self.repository = repository
        self.client_factory = client_factory

    def get_settings(self) -> Sub2Settings:
        """读取 Sub2API 配置，兼容环境变量和 SQLite 持久化配置。"""

        settings = get_settings()
        return Sub2Settings(
            apiUrl=trim_string(self.repository.get(API_URL_KEY) or settings.sub2_api_url),
            apiKey=trim_string(self.repository.get(API_KEY_KEY) or settings.sub2_api_key),
            defaultGroupId=parse_optional_group_id(self.repository.get(DEFAULT_GROUP_ID_KEY)),
            defaultProxyId=parse_optional_positive_id(self.repository.get(DEFAULT_PROXY_ID_KEY)),
            openAiAuthLoginEnabled=parse_bool_setting(self.repository.get(OPENAI_AUTH_LOGIN_ENABLED_KEY), True),
        )

    def to_public_settings(self, settings: Sub2Settings | None = None) -> Sub2PublicSettings:
        """转换为前端可展示的 Sub2API 配置。"""

        current = settings or self.get_settings()
        return Sub2PublicSettings(
            apiUrl=current.api_url,
            hasApiKey=bool(current.api_key),
            apiKeyPreview=mask_api_key(current.api_key),
            defaultGroupId=current.default_group_id,
            defaultProxyId=current.default_proxy_id,
            openAiAuthLoginEnabled=current.open_ai_auth_login_enabled,
        )

    def save_settings(self, update: Sub2SettingsUpdate) -> Sub2PublicSettings:
        """保存 Sub2API 配置。

        参数:
            update: 配置更新请求；未传字段保持现值。

        返回:
            脱敏后的公开配置。
        """

        current = self.get_settings()
        next_settings = Sub2Settings(
            apiUrl=current.api_url if update.api_url is None else update.api_url,
            apiKey=current.api_key if update.api_key is None else update.api_key,
            defaultGroupId=current.default_group_id
            if "default_group_id" not in update.model_fields_set
            else update.default_group_id,
            defaultProxyId=current.default_proxy_id
            if "default_proxy_id" not in update.model_fields_set
            else update.default_proxy_id,
            openAiAuthLoginEnabled=current.open_ai_auth_login_enabled
            if "open_ai_auth_login_enabled" not in update.model_fields_set
            else (
                update.open_ai_auth_login_enabled
                if update.open_ai_auth_login_enabled is not None
                else current.open_ai_auth_login_enabled
            ),
        )
        self.repository.set(API_URL_KEY, next_settings.api_url)
        self.repository.set(API_KEY_KEY, next_settings.api_key)
        self.repository.set(
            DEFAULT_GROUP_ID_KEY,
            "" if next_settings.default_group_id is None else str(next_settings.default_group_id),
        )
        self.repository.set(
            DEFAULT_PROXY_ID_KEY,
            "" if next_settings.default_proxy_id is None else str(next_settings.default_proxy_id),
        )
        self.repository.set(
            OPENAI_AUTH_LOGIN_ENABLED_KEY,
            "true" if next_settings.open_ai_auth_login_enabled else "false",
        )
        logger.info(
            "保存 Sub2API 配置：apiUrl=%s hasApiKey=%s defaultGroupId=%s defaultProxyId=%s openAiAuthLoginEnabled=%s",
            next_settings.api_url,
            bool(next_settings.api_key),
            next_settings.default_group_id,
            next_settings.default_proxy_id,
            next_settings.open_ai_auth_login_enabled,
        )
        return self.to_public_settings(next_settings)

    def _request(
        self,
        method: str,
        url: str,
        settings: Sub2Settings,
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """执行 Sub2API HTTP 请求，统一附加鉴权、超时和系统代理。

        参数:
            method: HTTP 方法。
            url: 请求地址。
            settings: 已校验的 Sub2API 配置。
            payload: JSON 请求体；为空时不设置 content-type。

        返回:
            httpx 原始响应对象。
        """

        network_settings = network_settings_service.get()
        timeout_seconds = network_settings.timeout_ms / 1000
        proxy = network_settings.proxy_url or None
        logger.debug("请求 Sub2API：method=%s url=%s hasPayload=%s proxy=%s", method, url, payload is not None, bool(proxy))
        with self.client_factory(timeout=timeout_seconds, proxy=proxy) as client:
            headers = sub2_auth_headers(settings.api_key)
            if payload is not None:
                headers = {"content-type": "application/json", **headers}
            return client.request(
                method,
                url,
                headers=headers,
                json=payload,
            )

    def _sub2_json_response(self, response: httpx.Response, prefix: str, fallback: str) -> Any:
        """解析 Sub2API 响应并统一转换错误。

        参数:
            response: httpx 响应对象。
            prefix: 错误消息前缀。
            fallback: 无法提取服务端消息时使用的兜底文案。

        返回:
            解析后的响应体。

        异常:
            RuntimeError: HTTP 非成功状态或业务 code 非 0。
        """

        body = parse_json_or_text(response.text)
        if not response.is_success:
            logger.error("Sub2API HTTP 请求失败：status=%s prefix=%s", response.status_code, prefix)
            raise RuntimeError(f"{prefix}：{sub2_error_message(body, response.reason_phrase or f'HTTP {response.status_code}')}")
        if isinstance(body, dict) and "code" in body:
            try:
                code = int(body["code"])
            except (TypeError, ValueError):
                code = 0
            if code != 0:
                logger.error("Sub2API 业务响应失败：code=%s prefix=%s", body.get("code"), prefix)
                raise RuntimeError(f"{prefix}：{sub2_error_message(body, fallback)}")
        return body

    def _require_settings(self) -> Sub2Settings:
        """读取并校验 Sub2API 必填配置。"""

        settings = self.get_settings()
        if not settings.api_key:
            raise ValueError("请先在系统设置里配置 Sub2API APIKey")
        return settings

    def fetch_groups(self) -> list[Sub2Group]:
        """从 Sub2API 获取 OpenAI 可用分组列表。"""

        settings = self._require_settings()
        logger.info("开始获取 Sub2 分组：apiUrl=%s", settings.api_url)
        response = self._request("GET", normalize_sub2_groups_url(settings.api_url), settings)
        body = self._sub2_json_response(response, "Sub2API 获取分组失败", "接口返回失败")
        groups = extract_sub2_groups(body)
        logger.info("Sub2 分组获取成功：count=%s", len(groups))
        return groups

    def fetch_proxies(self) -> list[Sub2Proxy]:
        """从 Sub2API 获取当前可用代理列表。"""

        settings = self._require_settings()
        logger.info("开始获取 Sub2 代理：apiUrl=%s", settings.api_url)
        response = self._request("GET", normalize_sub2_active_proxies_url(settings.api_url), settings)
        body = self._sub2_json_response(response, "Sub2API 获取代理失败", "接口返回失败")
        proxies = [
            Sub2Proxy(
                id=proxy["id"],
                name=proxy.get("name"),
                protocol=proxy.get("protocol"),
                host=proxy.get("host"),
                port=proxy.get("port"),
                username=proxy.get("username"),
            )
            for proxy in extract_sub2_proxies(body)
        ]
        logger.info("Sub2 代理获取成功：count=%s", len(proxies))
        return proxies

    def convert_account(self, input_value: Any) -> dict[str, Any]:
        """转换旧 ChatGPT session JSON 为 Sub2 导入数据。"""

        return convert_chat_gpt_session_to_sub2(input_value)

    def push_data(self, data: dict[str, Any], group_id: int | None = None, proxy_id: int | None = None) -> dict[str, Any]:
        """推送已经转换好的 Sub2 导入数据。

        参数:
            data: Sub2 导入数据，至少包含 accounts。
            group_id: 本次推送分组 ID；为空时使用默认分组。
            proxy_id: 用户选择的 Sub2 代理 ID；为空时按导入数据自动解析。

        返回:
            实际推送的数据和 Sub2API 创建账号响应。
        """

        settings = self._require_settings()
        resolved_group_id = self.resolve_push_group_id(group_id, settings)
        resolved_proxy_id = self.resolve_push_proxy_id(proxy_id)
        accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else []
        logger.info("开始推送 Sub2 账号：groupId=%s proxyId=%s accountCount=%s", resolved_group_id, resolved_proxy_id, len(accounts))
        proxied_data = data if resolved_proxy_id is not None else self.ensure_data_proxy(data, settings)
        prepared = apply_sub2_group(proxied_data, resolved_group_id)
        result = {
            "data": prepared,
            "response": self.create_accounts(prepared, resolved_group_id, settings, resolved_proxy_id),
        }
        logger.info("Sub2 账号推送完成：groupId=%s accountCount=%s", resolved_group_id, len(accounts))
        return result

    def push_data_via_auth_login(
        self,
        data: dict[str, Any],
        group_id: int | None,
        proxy_id: int | None,
        authorize: Callable[[Sub2AuthLoginRequest], Sub2AuthLoginCallback],
    ) -> dict[str, Any]:
        """通过 Sub2 OpenAI 授权登录分支创建账号。

        参数:
            data: 已转换的 Sub2 导入数据。
            group_id: 本次推送分组 ID；为空时使用默认分组。
            proxy_id: 本次推送代理 ID；为空时按导入数据自动解析。
            authorize: 使用当前 OpenAI 会话打开 Sub2 授权 URL 并返回 OAuth 回调参数。

        返回:
            实际推送数据和 Sub2API 响应。

        异常:
            Sub2AuthBranchFallbackError: 授权分支缺少 code/state 或遇到手机号步骤。
            RuntimeError: Sub2API 请求失败。
        """

        settings = self._require_settings()
        resolved_group_id = self.resolve_push_group_id(group_id, settings)
        resolved_proxy_id = self.resolve_push_proxy_id(proxy_id)
        prepared_data = data if resolved_proxy_id is not None else self.ensure_data_proxy(data, settings)
        prepared = apply_sub2_group(prepared_data, resolved_group_id)
        proxies = proxy_by_key(prepared)
        responses: list[Any] = []
        accounts = prepared.get("accounts") if isinstance(prepared.get("accounts"), list) else []
        logger.info("开始通过 Sub2 授权登录创建账号：groupId=%s proxyId=%s accountCount=%s", resolved_group_id, resolved_proxy_id, len(accounts))
        for account in accounts:
            if not isinstance(account, dict):
                continue
            proxy = proxies.get(str(account.get("proxy_key") or ""), {})
            account_proxy_id = resolved_proxy_id if resolved_proxy_id is not None else self.resolve_proxy_id(proxy, settings)
            generated_response = self._request(
                "POST",
                normalize_sub2_openai_auth_url(settings.api_url, "generate-auth-url"),
                settings,
                {"proxy_id": account_proxy_id} if account_proxy_id else {},
            )
            generated = self._sub2_json_response(generated_response, "Sub2API 生成 OpenAI 授权地址失败", "接口返回失败")
            auth_session = extract_sub2_auth_session(generated)
            credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
            callback = authorize(Sub2AuthLoginRequest(
                auth_url=auth_session["auth_url"],
                session_id=auth_session["session_id"],
                email=str(credentials.get("email") or account.get("name") or ""),
                account=account,
                proxy_id=account_proxy_id,
            ))
            state = callback.state or auth_url_state(auth_session["auth_url"])
            if not callback.code or not state:
                logger.warning("Sub2 授权登录缺少 code/state：sessionId=%s", auth_session["session_id"])
                raise Sub2AuthBranchFallbackError("Sub2 授权登录未拿到完整 OAuth callback code/state")
            payload: dict[str, Any] = {
                "session_id": auth_session["session_id"],
                "code": callback.code,
                "state": state,
                "name": account.get("name"),
                "concurrency": account.get("concurrency"),
                "priority": account.get("priority"),
                "group_ids": [resolved_group_id],
                "confirm_mixed_channel_risk": True,
            }
            if account_proxy_id:
                payload["proxy_id"] = account_proxy_id
            create_response = self._request(
                "POST",
                normalize_sub2_openai_auth_url(settings.api_url, "create-from-oauth"),
                settings,
                payload,
            )
            responses.append(self._sub2_json_response(create_response, "Sub2API 授权创建账号失败", "接口返回失败"))
            logger.info("Sub2 授权登录创建账号成功：name=%s groupId=%s proxyId=%s", account.get("name"), resolved_group_id, account_proxy_id)
        logger.info("Sub2 授权登录创建账号完成：groupId=%s accountCount=%s", resolved_group_id, len(accounts))
        return {"data": prepared, "response": responses}

    def resolve_push_group_id(self, group_id: int | None, settings: Sub2Settings) -> int:
        """解析本次推送应该使用的 Sub2 分组 ID。"""

        resolved = group_id if group_id is not None else settings.default_group_id
        if not isinstance(resolved, int) or resolved <= 0:
            raise ValueError("请先在系统设置里选择 Sub2 默认推送分组")
        return resolved

    def resolve_push_proxy_id(self, proxy_id: int | None) -> int | None:
        """解析本次推送应该使用的 Sub2 代理 ID。"""

        if proxy_id is None:
            return None
        if not isinstance(proxy_id, int) or proxy_id <= 0:
            raise ValueError("Sub2 默认代理 ID 无效")
        return proxy_id

    def fetch_default_proxy(self, settings: Sub2Settings) -> dict[str, Any]:
        """从 Sub2API 获取一个当前可用代理作为默认绑定代理。"""

        logger.info("开始获取 Sub2 默认代理：apiUrl=%s", settings.api_url)
        response = self._request("GET", normalize_sub2_default_proxy_list_url(settings.api_url), settings)
        body = self._sub2_json_response(response, "Sub2API 获取代理失败", "接口返回失败")
        proxy = next(iter(extract_sub2_proxies(body)), None)
        if proxy is None:
            logger.warning("Sub2API 没有返回可用代理")
            raise RuntimeError("Sub2API 没有可用代理，请先在 Sub2 后台添加并启用代理")
        result = to_sub2_proxy(proxy)
        logger.info("获取 Sub2 默认代理成功：proxyId=%s host=%s", result.get("id"), result.get("host"))
        return result

    def ensure_data_proxy(self, data: dict[str, Any], settings: Sub2Settings) -> dict[str, Any]:
        """确保每个待推送账号都有可用 proxy_key。

        说明:
            旧模板可能没有 proxy_key。这里会先复用输入数据里的代理；
            如果仍缺失，则从 Sub2API 获取已有可用代理并绑定，避免每次推送都创建新代理。
        """

        proxies = proxy_by_key(data)
        accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else []
        missing_proxy = any(
            not isinstance(account, dict)
            or not account.get("proxy_key")
            or account.get("proxy_key") not in proxies
            for account in accounts
        )
        if not missing_proxy:
            logger.debug("Sub2 导入数据已包含完整代理绑定：accountCount=%s", len(accounts))
            return data

        logger.warning("Sub2 导入数据缺少可用 proxy_key，开始补齐默认代理：accountCount=%s", len(accounts))
        configured_proxy = next(
            (
                proxy for proxy in data.get("proxies", [])
                if isinstance(proxy, dict)
                and isinstance(proxy.get("proxy_key"), str)
                and proxy["proxy_key"].strip()
            ),
            None,
        )
        fallback_proxy = configured_proxy or self.fetch_default_proxy(settings)
        fallback_key = str(fallback_proxy.get("proxy_key") or "").strip()
        if not fallback_key:
            logger.error("Sub2 默认代理缺少 proxy_key：proxyId=%s", fallback_proxy.get("id"))
            raise RuntimeError("Sub2API 可用代理缺少可绑定的 proxy_key")

        next_proxy = {**fallback_proxy, "proxy_key": fallback_key}
        next_proxies = data.get("proxies") if isinstance(data.get("proxies"), list) else []
        if fallback_key not in proxies:
            next_proxies = [*next_proxies, next_proxy]

        logger.info(
            "Sub2 账号代理绑定完成：hasProxyKey=%s proxyId=%s accountCount=%s",
            bool(fallback_key),
            next_proxy.get("id"),
            len(accounts),
        )
        return {
            **data,
            "proxies": next_proxies,
            "accounts": [
                account
                if isinstance(account, dict) and account.get("proxy_key") in proxies
                else {**account, "proxy_key": fallback_key}
                for account in accounts
                if isinstance(account, dict)
            ],
        }

    def resolve_proxy_id(self, proxy: dict[str, Any], settings: Sub2Settings) -> int | None:
        """解析 Sub2 账号创建接口需要的 proxy_id。

        参数:
            proxy: 导入数据中的代理对象。
            settings: Sub2API 配置。

        返回:
            可绑定的 Sub2 proxy_id；缺少代理信息时返回 None。
        """

        proxy_id = number_field(proxy, "id")
        if proxy_id is not None and proxy_id > 0:
            logger.debug("复用代理 ID：proxyId=%s", proxy_id)
            return proxy_id
        if not proxy.get("host") or not proxy.get("protocol") or not proxy.get("port"):
            logger.warning("Sub2 账号未提供完整代理信息，将不绑定 proxy_id")
            return None

        logger.info("查询 Sub2 现有代理：protocol=%s host=%s port=%s", proxy.get("protocol"), proxy.get("host"), proxy.get("port"))
        list_response = self._request("GET", normalize_sub2_proxy_list_url(settings.api_url, proxy), settings)
        list_body = self._sub2_json_response(list_response, "Sub2API 查询代理失败", "接口返回失败")
        candidates = extract_sub2_proxies(list_body)
        existing = next((item["id"] for item in candidates if proxy_matches(item, proxy)), None)
        if existing is None and candidates:
            existing = candidates[0]["id"]
        if existing:
            logger.info("复用 Sub2 现有代理：proxyId=%s candidateCount=%s", existing, len(candidates))
            return existing

        logger.info("未找到匹配 Sub2 代理，开始创建：protocol=%s host=%s port=%s", proxy.get("protocol"), proxy.get("host"), proxy.get("port"))
        create_response = self._request(
            "POST",
            normalize_sub2_proxies_url(settings.api_url),
            settings,
            {
                "name": str(proxy.get("name") or proxy.get("host")),
                "protocol": str(proxy.get("protocol")),
                "host": str(proxy.get("host")),
                "port": number_field(proxy, "port"),
                "username": str(proxy.get("username") or ""),
                "password": str(proxy.get("password") or ""),
            },
        )
        create_body = self._sub2_json_response(create_response, "Sub2API 创建代理失败", "接口返回失败")
        data = unwrap_sub2_data(create_body)
        created_id = number_field(data, "id") if isinstance(data, dict) else None
        logger.info("Sub2 代理创建完成：proxyId=%s", created_id)
        return created_id

    def create_accounts(
        self,
        data: dict[str, Any],
        group_id: int,
        settings: Sub2Settings,
        selected_proxy_id: int | None = None,
    ) -> list[Any]:
        """逐个调用 Sub2API 创建 OpenAI 账号。

        参数:
            data: 已补齐代理和分组的 Sub2 导入数据。
            group_id: 本次推送目标分组。
            settings: Sub2API 配置。
            selected_proxy_id: 用户指定的代理 ID；为空时根据导入数据自动解析。

        返回:
            每个账号创建接口的响应体。
        """

        proxies = proxy_by_key(data)
        responses: list[Any] = []
        for account in data.get("accounts") if isinstance(data.get("accounts"), list) else []:
            if not isinstance(account, dict):
                continue
            proxy = proxies.get(str(account.get("proxy_key") or ""), {})
            proxy_id = selected_proxy_id if selected_proxy_id is not None else self.resolve_proxy_id(proxy, settings)
            logger.info(
                "创建 Sub2 账号：name=%s groupId=%s proxyId=%s",
                account.get("name"),
                group_id,
                proxy_id,
            )
            payload: dict[str, Any] = {
                "name": account.get("name"),
                "platform": account.get("platform"),
                "type": account.get("type"),
                "credentials": account.get("credentials"),
                "extra": account.get("extra"),
                "concurrency": account.get("concurrency"),
                "priority": account.get("priority"),
                "rate_multiplier": account.get("rate_multiplier"),
                "auto_pause_on_expired": account.get("auto_pause_on_expired"),
                "group_ids": [group_id],
                "confirm_mixed_channel_risk": True,
            }
            notes = str(account.get("notes") or "").strip()
            if notes:
                payload["notes"] = notes
            if proxy_id:
                payload["proxy_id"] = proxy_id
            response = self._request("POST", normalize_sub2_accounts_url(settings.api_url), settings, payload)
            responses.append(self._sub2_json_response(response, "Sub2API 创建账号失败", "接口返回失败"))
            logger.info("Sub2 账号创建成功：name=%s groupId=%s", account.get("name"), group_id)
        return responses

    def push_account(self, input_value: Any, group_id: int | None = None) -> dict[str, Any]:
        """转换并推送单个旧 ChatGPT session 账号。"""

        return self.push_data(self.convert_account(input_value), group_id)


sub2_service = Sub2Service()
