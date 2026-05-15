from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from app.db.duck_repository import DuckRepository, duck_repository
from app.db.mail_repository import MailRepository, mail_repository
from app.services.claw_mail import ClawMailClient, claw_mail_client, mail_to_repository_input
from app.services.network_settings import NetworkSettingsService, network_settings_service


logger = logging.getLogger(__name__)
AUTH_BASE = "https://auth.openai.com"
PLATFORM_BASE = "https://platform.openai.com"
CHATGPT_BASE = "https://chatgpt.com"
PLATFORM_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
PLATFORM_REDIRECT_URI = f"{PLATFORM_BASE}/auth/callback"
PLATFORM_AUDIENCE = "https://api.openai.com/v1"
PLATFORM_AUTH0_CLIENT = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
SEC_CH_UA = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
SEC_CH_UA_FULL = '"Chromium";v="145.0.0.0", "Not:A-Brand";v="99.0.0.0", "Google Chrome";v="145.0.0.0"'
CODE_PATTERN = re.compile(r"\b(\d{6})\b")
HTML_CODE_BLOCK_PATTERN = re.compile(r"<p\b[^>]*font-size:\s*24px[^>]*>\s*(\d{6})\s*</p>", re.I)
STANDALONE_CODE_PATTERN = re.compile(r"(?:^|[\s>])(\d{6})(?:[\s<]|$)")
OPENAI_CODE_SUBJECT_PATTERN = re.compile(r"(openai|chatgpt|chat gpt|验证码|verification|code|login)", re.I)
PHONE_REQUIREMENT_PATTERN = re.compile(r"add[-_ ]?phone|phone[_-]?verification|phone[_-]?number|phone_required|绑定手机号|手机", re.I)
OTP_MAIL_TIME_GRACE_MS = 90_000
OTP_MAIL_FALLBACK_ACCEPT_MS = 15 * 60_000
OTP_MAIL_SCAN_LIMIT = 100
OTP_MAIL_FAST_POLL_WINDOW_MS = 30_000
OTP_MAIL_FAST_POLL_INTERVAL_SECONDS = 2
OTP_MAIL_SLOW_POLL_INTERVAL_SECONDS = 5


@dataclass(slots=True)
class OpenAiAuthProgress:
    """OpenAI 自动登录进度上下文。"""

    operation_id: str
    started_at: float
    email: str
    inbox_email: str


@dataclass(slots=True)
class OpenAiLoginResult:
    """OpenAI 登录结果，包含 OAuth token 和可复用会话。"""

    token: dict[str, Any]
    client: OpenAiAuthClient
    device_id: str


@dataclass(slots=True)
class PasswordVerifyResult:
    """OpenAI 密码校验结果。"""

    next_url: str
    page_type: str
    requires_email_otp: bool
    otp_requested_at_ms: int | None = None


@dataclass(slots=True)
class VerificationCodeCandidate:
    """邮箱验证码候选项。"""

    code: str
    provider_mail_id: str
    mail_time: int


def base64_url(data: bytes) -> str:
    """生成不带填充的 base64url 字符串。"""

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_pkce() -> dict[str, str]:
    """生成 OpenAI OAuth PKCE verifier/challenge。"""

    verifier = base64_url(random.randbytes(64))
    challenge = base64_url(hashlib.sha256(verifier.encode("ascii")).digest())
    return {"verifier": verifier, "challenge": challenge}


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """解析 JWT payload；失败时返回空对象。"""

    try:
        payload = token.split(".")[1]
        padded = payload + "=" * ((4 - len(payload) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        parsed = json.loads(decoded)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def create_operation_id() -> str:
    """生成日志操作 ID。"""

    return uuid.uuid4().hex[:8]


def normalize_email(value: str) -> str:
    """归一化邮箱地址。"""

    return value.strip().lower()


def log_progress(progress: OpenAiAuthProgress | None, stage: str, extra: dict[str, Any] | None = None, level: str = "info") -> None:
    """记录 OpenAI 推送关键阶段日志。"""

    if progress is None:
        return
    payload = {
        "operation": progress.operation_id,
        "stage": stage,
        "elapsedMs": round((time.time() - progress.started_at) * 1000),
        "email": progress.email,
        "inboxEmail": progress.inbox_email,
        **(extra or {}),
    }
    message = "OpenAI 推送阶段：%s", payload
    if level == "error":
        logger.error(*message)
    elif level == "warn":
        logger.warning(*message)
    else:
        logger.info(*message)


def json_or_none(text: str) -> Any:
    """将文本解析为 JSON；空文本或非 JSON 返回 None。"""

    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def as_record(value: Any) -> dict[str, Any]:
    """确保值是 JSON 对象，不是则返回空对象。"""

    return value if isinstance(value, dict) else {}


def string_field(record: dict[str, Any], key: str) -> str:
    """从对象中读取字符串字段并裁剪空白。"""

    value = record.get(key)
    return value.strip() if isinstance(value, str) else ""


def body_error(body: Any, fallback: str) -> str:
    """从 OpenAI 响应体中提取错误消息。"""

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        for key in ("message", "error_description", "detail", "reason"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


def page_type(body: Any) -> str:
    """读取 OpenAI 授权页类型。"""

    root = as_record(body)
    page = root.get("page")
    if isinstance(page, str):
        return page.strip()
    return string_field(as_record(page), "type")


def continue_url(body: Any) -> str:
    """读取 OpenAI 响应中的 continue_url。"""

    return string_field(as_record(body), "continue_url")


def auth_step(body: Any) -> str:
    """读取 OpenAI 授权流程当前步骤。"""

    root = as_record(body)
    for key in ("step", "action", "screen", "state", "method"):
        value = string_field(root, key)
        if value:
            return value
    flow = as_record(root.get("flow"))
    return string_field(flow, "step") or string_field(flow, "name")


def requires_account_profile(page: str) -> bool:
    """判断是否需要补充账号资料。"""

    return page in {"account_details", "about_you"}


def summarize_auth_body(body: Any) -> dict[str, Any]:
    """生成 OpenAI 授权响应的安全摘要。"""

    root = as_record(body)
    return {
        "pageType": page_type(body),
        "continueUrl": continue_url(body),
        "step": auth_step(body),
        "hasError": bool(root.get("error")),
        "keys": list(root.keys())[:12],
    }


def requires_email_otp_step(body: Any) -> bool:
    """判断当前响应是否需要邮箱 OTP。"""

    values = " ".join([
        page_type(body),
        continue_url(body),
        auth_step(body),
        json.dumps(summarize_auth_body(body), ensure_ascii=False),
    ])
    return re.search(r"email[_-]?otp|email[_-]?verification|verification[_-]?code|verify[_-]?email|mfa", values, re.I) is not None


def callback_params_from_url(url: str) -> dict[str, str] | None:
    """从 OAuth 回调 URL 中提取 code/state/scope。"""

    try:
        parsed = urlparse(url)
        params = dict(parse_qsl(parsed.query))
        code = params.get("code", "").strip()
        if not code:
            return None
        return {
            "code": code,
            "state": params.get("state", "").strip(),
            "scope": params.get("scope", "").strip(),
        }
    except Exception:
        return None


def contains_phone_requirement(value: Any) -> bool:
    """判断文本或对象中是否出现手机号绑定步骤。"""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return PHONE_REQUIREMENT_PATTERN.search(text) is not None


def phone_requirement_hint(value: Any) -> str:
    """提取手机号步骤提示关键词。"""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    match = PHONE_REQUIREMENT_PATTERN.search(text)
    return match.group(0) if match else ""


def safe_url_summary(value: str | None) -> dict[str, Any]:
    """生成不泄露敏感 query 的 URL 摘要。"""

    raw = (value or "").strip()
    if not raw:
        return {}
    try:
        parsed = urlparse(urljoin(AUTH_BASE, raw))
        params = dict(parse_qsl(parsed.query))
        return {
            "host": parsed.netloc,
            "path": parsed.path,
            "hasCode": bool(params.get("code")),
            "hasState": bool(params.get("state")),
        }
    except Exception:
        return {"raw": raw[:120]}


def is_unsupported_chatgpt_region_response(response: httpx.Response) -> bool:
    """判断 ChatGPT 响应是否表示当前地区不支持。"""

    body = response.text[:200_000]
    return response.status_code == 403 or re.search(
        r"not available|unsupported (country|region)|not supported in (your )?(country|region)|services are not available|access denied|地区|区域|所在国家|不支持|不可用",
        body,
        re.I,
    ) is not None


def decode_base64_url_json(value: str) -> dict[str, Any] | None:
    """解析 base64url JSON 字符串。"""

    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def first_workspace_id(client_session: str | None) -> str:
    """从 oai-client-auth-session cookie 中提取第一个 workspace ID。"""

    first_part = (client_session or "").split(".")[0]
    payload = decode_base64_url_json(first_part) if first_part else None
    workspaces = payload.get("workspaces") if isinstance(payload, dict) else []
    first = workspaces[0] if isinstance(workspaces, list) and workspaces else None
    return string_field(first, "id") if isinstance(first, dict) else ""


def extract_verification_code(input_value: dict[str, str | None]) -> str | None:
    """从邮件主题、纯文本和 HTML 中提取 OpenAI 六位验证码。"""

    subject = input_value.get("subject") or ""
    text = input_value.get("text") or ""
    html = input_value.get("html") or ""
    haystack = "\n".join(item for item in (subject, text, html) if item)
    if not OPENAI_CODE_SUBJECT_PATTERN.search(subject) and re.search(r"openai|chatgpt|chat gpt", haystack, re.I) is None:
        return None
    stripped_html = re.sub(r"<[^>]+>", " ", html)
    for pattern, source in (
        (HTML_CODE_BLOCK_PATTERN, html),
        (STANDALONE_CODE_PATTERN, text),
        (STANDALONE_CODE_PATTERN, subject),
        (STANDALONE_CODE_PATTERN, stripped_html),
        (CODE_PATTERN, "\n".join([subject, text])),
    ):
        match = pattern.search(source)
        if match:
            return match.group(1)
    return None


def mail_addresses(value: list[str] | None) -> list[str]:
    """从邮件地址字段中提取邮箱地址列表。"""

    addresses: list[str] = []
    for item in value or []:
        addresses.extend(match.lower() for match in re.findall(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", item, re.I))
    return addresses


def mail_targets_expected_address(mail: dict[str, Any], target_email: str, inbox_email: str) -> bool:
    """判断验证码邮件是否发给目标 Duck 邮箱或绑定收件箱。"""

    expected = {normalize_email(target_email), normalize_email(inbox_email)}
    recipients = [
        *mail_addresses(mail.get("to")),
        *mail_addresses(mail.get("cc")),
        *mail_addresses(mail.get("bcc")),
        *mail_addresses([mail.get("headerRaw")] if mail.get("headerRaw") else None),
    ]
    return not recipients or any(email in expected for email in recipients)


def parse_mail_time(value: str | None) -> int | None:
    """解析邮件时间为毫秒时间戳。"""

    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except Exception:
        pass
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def mail_summary_id(summary: Any) -> str | None:
    """从 Coremail 邮件摘要中读取邮件 ID。"""

    if isinstance(summary, dict) and summary.get("id") is not None:
        return str(summary["id"])
    if summary is not None:
        return str(summary)
    return None


def mail_summary_time(summary: Any) -> int | None:
    """从 Coremail 邮件摘要中读取邮件时间。"""

    if not isinstance(summary, dict):
        return None
    for key in ("sentDate", "date", "receivedDate", "receivedAt", "createdAt"):
        parsed = parse_mail_time(str(summary.get(key) or ""))
        if parsed is not None:
            return parsed
    return None


def mail_summary_unread(summary: Any) -> bool | None:
    """从 Coremail 邮件摘要中读取未读状态，字段缺失时返回 None。"""

    if not isinstance(summary, dict):
        return None
    for key in ("read", "isRead", "readFlag", "seen", "isSeen"):
        if key in summary:
            return not bool(summary[key])
    for key in ("unread", "isUnread", "new", "isNew"):
        if key in summary:
            return bool(summary[key])
    return None


def cached_mail_to_openai_mail(mail: dict[str, Any]) -> dict[str, Any]:
    """将本地邮件缓存转换为验证码解析所需结构。"""

    try:
        raw = json.loads(mail.get("raw_json") or "{}")
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        **raw,
        "id": raw.get("id") or mail.get("provider_mail_id"),
        "subject": raw.get("subject") or mail.get("subject"),
        "text": raw.get("text") if isinstance(raw.get("text"), dict) else {"content": mail.get("text")},
        "html": raw.get("html") if isinstance(raw.get("html"), dict) else {"content": mail.get("html")},
        "date": raw.get("date") or mail.get("received_at"),
        "to": raw.get("to") or ([mail.get("address")] if mail.get("address") else None),
        "headerRaw": raw.get("headerRaw") or mail.get("header_raw"),
    }


def random_password(length: int = 18) -> str:
    """生成 OpenAI 注册密码。"""

    upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lower = "abcdefghijklmnopqrstuvwxyz"
    digits = "0123456789"
    symbols = "!@#$%"
    all_chars = upper + lower + digits + symbols
    chars = [
        random.choice(upper),
        random.choice(lower),
        random.choice(digits),
        random.choice(symbols),
    ]
    while len(chars) < length:
        chars.append(random.choice(all_chars))
    random.shuffle(chars)
    return "".join(chars)


def random_profile() -> dict[str, str]:
    """生成 OpenAI 账号资料。"""

    first = ["James", "Robert", "John", "Michael", "David", "Mary", "Emma", "Olivia"]
    last = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
    year = 1996 + random.randrange(11)
    month = f"{1 + random.randrange(12):02d}"
    day = f"{1 + random.randrange(28):02d}"
    return {"name": f"{random.choice(first)} {random.choice(last)}", "birthdate": f"{year}-{month}-{day}"}


def serialize_openai_auth_json(token: dict[str, Any]) -> str:
    """序列化要保存到 Duck 地址的 OpenAI OAuth JSON。"""

    return json.dumps({
        "email": token.get("email"),
        "accessToken": token.get("accessToken"),
        "refreshToken": token.get("refreshToken"),
        "idToken": token.get("idToken"),
        "expiresAt": token.get("expiresAt"),
        "userId": token.get("userId"),
        "accountId": token.get("accountId"),
        "planType": token.get("planType"),
    }, ensure_ascii=False, indent=2)


class CookieJar:
    """简单 CookieJar，用于复用 OpenAI 授权流程 Cookie。"""

    def __init__(self) -> None:
        """初始化 Cookie 存储。"""

        self._cookies: dict[str, dict[str, str]] = {}

    def header(self, url: str) -> str:
        """生成指定 URL 可用的 Cookie 请求头。"""

        target = urlparse(url)
        values: list[str] = []
        for cookie in self._cookies.values():
            domain = cookie["domain"]
            path = cookie["path"]
            host_matches = target.hostname.endswith(domain[1:]) if domain.startswith(".") else target.hostname == domain
            if host_matches and target.path.startswith(path):
                values.append(f"{cookie['name']}={cookie['value']}")
        return "; ".join(values)

    def store(self, url: str, response: httpx.Response) -> None:
        """保存响应中的 Set-Cookie。"""

        target = urlparse(url)
        for value in response.headers.get_list("set-cookie"):
            cookie = SimpleCookie()
            cookie.load(value)
            for morsel in cookie.values():
                domain = (morsel["domain"] or target.hostname or "").lower()
                path = morsel["path"] or "/"
                key = f"{domain}|{path}|{morsel.key}"
                self._cookies[key] = {
                    "name": morsel.key,
                    "value": morsel.value,
                    "domain": domain,
                    "path": path,
                }

    def get(self, name: str) -> str | None:
        """按名称读取最近保存的 Cookie 值。"""

        for cookie in self._cookies.values():
            if cookie["name"] == name:
                return cookie["value"]
        return None


class SentinelTokenGenerator:
    """生成 OpenAI Sentinel token 需要的浏览器指纹载荷。"""

    def __init__(self) -> None:
        """初始化随机 sid。"""

        self.sid = str(uuid.uuid4())

    @staticmethod
    def fnv1a32(text: str) -> str:
        """计算旧 Node 实现兼容的 32 位 FNV1a hash。"""

        value = 2166136261
        for char in text:
            value ^= ord(char)
            value = (value * 16777619) & 0xFFFFFFFF
        value ^= value >> 16
        value = (value * 2246822507) & 0xFFFFFFFF
        value ^= value >> 13
        value = (value * 3266489909) & 0xFFFFFFFF
        value ^= value >> 16
        return f"{value & 0xFFFFFFFF:08x}"

    def config(self, counter: int = 1, elapsed_ms: int | None = None) -> list[Any]:
        """构造 Sentinel 指纹数组。"""

        perf = random.random() * 49_000 + 1_000
        return [
            "1920x1080",
            datetime.now().astimezone().ctime(),
            4294705152,
            counter,
            USER_AGENT,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None,
            None,
            "en-US",
            elapsed_ms if elapsed_ms is not None else round(random.random() * 50) + 5,
            "hardwareConcurrency-undefined",
            "documentURI",
            "Object",
            perf,
            self.sid,
            "",
            8,
            int(time.time() * 1000 - perf),
        ]

    def b64(self, data: Any) -> str:
        """将 JSON 数据转为 base64。"""

        return base64.b64encode(json.dumps(data, separators=(",", ":")).encode("utf-8")).decode("ascii")

    def requirements_token(self) -> str:
        """生成 Sentinel requirements token。"""

        return f"gAAAAAC{self.b64(self.config())}"

    def proof_token(self, seed: str, difficulty: str) -> str:
        """根据 Sentinel PoW 要求生成 proof token。"""

        started_at = time.time()
        target = difficulty or "0"
        for index in range(500_000):
            payload = self.b64(self.config(index, round((time.time() - started_at) * 1000)))
            if self.fnv1a32(seed + payload)[:len(target)] <= target:
                return f"gAAAAAB{payload}~S"
        return self.requirements_token()


class OpenAiAuthClient:
    """OpenAI 授权流程 HTTP 客户端。"""

    def __init__(
        self,
        network_service: NetworkSettingsService = network_settings_service,
        client_factory: type[httpx.Client] = httpx.Client,
    ) -> None:
        """初始化客户端，统一使用系统代理和 CookieJar。"""

        self.network_service = network_service
        self.client_factory = client_factory
        self.jar = CookieJar()

    def request(self, url: str, options: dict[str, Any] | None = None) -> httpx.Response:
        """执行 OpenAI 授权 HTTP 请求并手动处理有限重定向。"""

        options = options or {}
        method = options.get("method") or "GET"
        referer = options.get("referer")
        headers = {**self.base_headers(url, referer), **(options.get("headers") or {})}
        cookie = self.jar.header(url)
        if cookie:
            headers["cookie"] = cookie
        body = options.get("body", None)
        content: str | None = None
        json_body: Any = None
        if body is not None:
            if isinstance(body, str):
                content = body
            else:
                json_body = body
        network = self.network_service.get()
        with self.client_factory(
            timeout=network.timeout_ms / 1000,
            proxy=network.proxy_url or None,
            follow_redirects=False,
        ) as client:
            response = client.request(method, url, headers=headers, content=content, json=json_body)
        self.jar.store(url, response)
        location = response.headers.get("location")
        redirect_limit = int(options.get("redirectLimit") or options.get("redirect_limit") or 0)
        if location and 300 <= response.status_code < 400 and redirect_limit > 0:
            next_url = urljoin(url, location)
            return self.request(next_url, {
                "method": "GET",
                "referer": url,
                "redirectLimit": redirect_limit - 1,
            })
        return response

    def base_headers(self, url: str, referer: str | None = None) -> dict[str, str]:
        """构造 OpenAI 授权流程通用浏览器请求头。"""

        endpoint = urlparse(url)
        headers = {
            "accept": "application/json,text/plain,*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": f"{endpoint.scheme}://{endpoint.netloc}",
            "user-agent": USER_AGENT,
            "sec-ch-ua": SEC_CH_UA,
            "sec-ch-ua-arch": '"x86_64"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version-list": SEC_CH_UA_FULL,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-model": '""',
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-platform-version": '"10.0.0"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "traceparent": f"00-{uuid.uuid4().hex}-{random.randbytes(8).hex()}-01",
            "tracestate": "dd=s:1;o:rum",
            "x-datadog-origin": "rum",
            "x-datadog-parent-id": str(random.randrange(1, 2**63)),
            "x-datadog-sampling-priority": "1",
            "x-datadog-trace-id": str(random.randrange(1, 2**63)),
        }
        if referer:
            headers["referer"] = referer
        return headers

    def cookie(self, name: str) -> str | None:
        """读取指定 Cookie。"""

        return self.jar.get(name)


class OpenAiAuthService:
    """OpenAI 自动登录、OTP 验证和 OAuth token 换取服务。"""

    def __init__(
        self,
        duck_repo: DuckRepository = duck_repository,
        mail_repo: MailRepository = mail_repository,
        mail_client: ClawMailClient = claw_mail_client,
        network_service: NetworkSettingsService = network_settings_service,
        client_factory: type[httpx.Client] = httpx.Client,
    ) -> None:
        """初始化 OpenAI 授权服务。"""

        self.duck_repo = duck_repo
        self.mail_repo = mail_repo
        self.mail_client = mail_client
        self.network_service = network_service
        self.client_factory = client_factory

    def create_client(self) -> OpenAiAuthClient:
        """创建 OpenAI 授权客户端，方便测试替换。"""

        return OpenAiAuthClient(self.network_service, self.client_factory)

    def build_sentinel_token(self, client: OpenAiAuthClient, device_id: str, flow: str) -> str:
        """请求并生成 OpenAI Sentinel token。"""

        generator = SentinelTokenGenerator()
        response = client.request("https://sentinel.openai.com/backend-api/sentinel/req", {
            "method": "POST",
            "headers": {
                "content-type": "text/plain;charset=UTF-8",
                "origin": "https://sentinel.openai.com",
            },
            "body": json.dumps({"p": generator.requirements_token(), "id": device_id, "flow": flow}, separators=(",", ":")),
            "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
        })
        body = json_or_none(response.text)
        if response.status_code != 200 or not isinstance(body, dict):
            raise RuntimeError(f"sentinel token 获取失败：HTTP {response.status_code}")
        token = string_field(body, "token")
        if not token:
            raise RuntimeError("sentinel token 响应为空")
        pow_body = body.get("proofofwork") if isinstance(body.get("proofofwork"), dict) else {}
        required = bool(pow_body.get("required")) if isinstance(pow_body, dict) else False
        seed = string_field(pow_body, "seed") if isinstance(pow_body, dict) else ""
        difficulty = string_field(pow_body, "difficulty") if isinstance(pow_body, dict) else "0"
        return json.dumps({
            "p": generator.proof_token(seed, difficulty) if required and seed else generator.requirements_token(),
            "t": "",
            "c": token,
            "id": device_id,
            "flow": flow,
        }, separators=(",", ":"))

    def assert_chatgpt_region_available(self, client: OpenAiAuthClient, progress: OpenAiAuthProgress | None = None) -> None:
        """检查当前系统代理是否能访问 ChatGPT。"""

        log_progress(progress, "chatgpt_region_check_start")
        try:
            response = client.request(CHATGPT_BASE, {"referer": CHATGPT_BASE, "redirectLimit": 3})
        except Exception as exc:
            raise RuntimeError(f"ChatGPT 代理检测失败：无法通过当前系统代理访问 chatgpt.com，{exc}") from exc
        if is_unsupported_chatgpt_region_response(response):
            raise RuntimeError("ChatGPT 代理检测失败：当前代理出口地区不支持访问 ChatGPT，请更换可访问 ChatGPT 的代理后再登录或注册")
        if response.status_code >= 500:
            raise RuntimeError(f"ChatGPT 代理检测失败：chatgpt.com 返回 HTTP {response.status_code}，请稍后重试或更换代理")
        log_progress(progress, "chatgpt_region_check_success", {"status": response.status_code})

    def register_password(
        self,
        client: OpenAiAuthClient,
        device_id: str,
        email: str,
        password: str,
        duck_address_id: int,
        progress: OpenAiAuthProgress | None = None,
    ) -> None:
        """为新 OpenAI 账号提交密码并保存到 Duck 地址。"""

        log_progress(progress, "register_password_start")
        response = client.request(f"{AUTH_BASE}/api/accounts/user/register", {
            "method": "POST",
            "body": {"username": email, "password": password},
            "referer": f"{AUTH_BASE}/create-account/password",
            "headers": {
                "oai-device-id": device_id,
                "openai-sentinel-token": self.build_sentinel_token(client, device_id, "username_password_create"),
            },
        })
        body = json_or_none(response.text)
        if response.status_code != 200:
            raise RuntimeError(f"OpenAI 注册密码提交失败：{body_error(body, response.reason_phrase or f'HTTP {response.status_code}')}")
        self.duck_repo.update_openai_credentials(duck_address_id, {"password": password})
        log_progress(progress, "register_password_success", {"status": response.status_code})

    def verify_password(
        self,
        client: OpenAiAuthClient,
        device_id: str,
        email: str,
        password: str,
        progress: OpenAiAuthProgress | None = None,
    ) -> PasswordVerifyResult:
        """校验已保存的 OpenAI 密码，必要时发送邮箱 OTP。"""

        log_progress(progress, "password_verify_start")
        response = client.request(f"{AUTH_BASE}/api/accounts/password/verify", {
            "method": "POST",
            "body": {"password": password},
            "referer": f"{AUTH_BASE}/log-in/password",
            "headers": {
                "oai-device-id": device_id,
                "openai-sentinel-token": self.build_sentinel_token(client, device_id, "password_verify"),
            },
        })
        body = json_or_none(response.text)
        if response.status_code != 200:
            raise RuntimeError(f"OpenAI 密码校验失败：{body_error(body, response.reason_phrase or f'HTTP {response.status_code}')}")
        log_progress(progress, "password_verify_success", {"status": response.status_code, **summarize_auth_body(body)})
        next_url = continue_url(body)
        next_page_type = page_type(body)
        requires_email_otp = not requires_account_profile(next_page_type) and (
            requires_email_otp_step(body) or not callback_params_from_url(next_url or "")
        )
        otp_requested_at_ms: int | None = None
        if requires_email_otp:
            otp_requested_at_ms = int(time.time() * 1000)
            self.send_email_otp(client, device_id, next_url or f"{AUTH_BASE}/email-verification", progress)
        return PasswordVerifyResult(
            next_url=next_url or f"{AUTH_BASE}/sign-in-with-chatgpt/codex/consent",
            page_type=next_page_type,
            requires_email_otp=requires_email_otp,
            otp_requested_at_ms=otp_requested_at_ms,
        )

    def send_email_otp(self, client: OpenAiAuthClient, device_id: str, referer: str, progress: OpenAiAuthProgress | None = None) -> None:
        """请求 OpenAI 发送邮箱 OTP。"""

        log_progress(progress, "otp_send_start", {"referer": referer})
        response = client.request(f"{AUTH_BASE}/api/accounts/email-otp/send", {
            "referer": referer,
            "headers": {"oai-device-id": device_id},
        })
        body = json_or_none(response.text)
        if response.status_code not in {200, 302}:
            log_progress(progress, "otp_send_retry_with_sentinel", {"status": response.status_code, **summarize_auth_body(body)}, "warn")
            response = client.request(f"{AUTH_BASE}/api/accounts/email-otp/send", {
                "referer": referer,
                "headers": {
                    "oai-device-id": device_id,
                    "openai-sentinel-token": self.build_sentinel_token(client, device_id, "authorize_continue"),
                },
            })
            body = json_or_none(response.text)
        if response.status_code not in {200, 302}:
            raise RuntimeError(f"OpenAI 发送验证码失败：{body_error(body, response.reason_phrase or f'HTTP {response.status_code}')}")
        log_progress(progress, "otp_send_success", {"status": response.status_code})

    def create_account_profile(self, client: OpenAiAuthClient, device_id: str, progress: OpenAiAuthProgress | None = None) -> str:
        """补充 OpenAI 新账号资料并返回下一跳 URL。"""

        profile = random_profile()
        log_progress(progress, "create_account_profile_start", {"birthdate": profile["birthdate"]})
        response = client.request(f"{AUTH_BASE}/api/accounts/create_account", {
            "method": "POST",
            "body": profile,
            "referer": f"{AUTH_BASE}/about-you",
            "headers": {
                "oai-device-id": device_id,
                "openai-sentinel-token": self.build_sentinel_token(client, device_id, "oauth_create_account"),
            },
        })
        body = json_or_none(response.text)
        if response.status_code not in {200, 302}:
            raise RuntimeError(f"OpenAI 创建账号资料失败：{body_error(body, response.reason_phrase or f'HTTP {response.status_code}')}")
        next_url = continue_url(body)
        log_progress(progress, "create_account_profile_success", {"status": response.status_code, "continueUrl": next_url})
        return next_url

    def exchange_tokens(self, client: OpenAiAuthClient, code: str, code_verifier: str) -> dict[str, Any]:
        """用 OAuth callback code 换取 OpenAI token。"""

        response = client.request(f"{AUTH_BASE}/oauth/token", {
            "method": "POST",
            "headers": {"content-type": "application/x-www-form-urlencoded"},
            "body": urlencode({
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": PLATFORM_REDIRECT_URI,
                "client_id": PLATFORM_CLIENT_ID,
                "code_verifier": code_verifier,
            }),
            "referer": PLATFORM_BASE,
        })
        data = json_or_none(response.text)
        if response.status_code != 200 or not isinstance(data, dict):
            raise RuntimeError(f"OpenAI token 换取失败：{body_error(data, response.reason_phrase or f'HTTP {response.status_code}')}")
        access_token = string_field(data, "access_token")
        refresh_token = string_field(data, "refresh_token")
        id_token = string_field(data, "id_token")
        if not access_token:
            raise RuntimeError("OpenAI token 响应缺少 access_token")
        payload = decode_jwt_payload(id_token) or decode_jwt_payload(access_token)
        expires_at: str | None = None
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)):
            expires_at = datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        elif isinstance(payload.get("exp"), (int, float)):
            expires_at = datetime.fromtimestamp(float(payload["exp"]), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "email": payload.get("email") if isinstance(payload.get("email"), str) else "",
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "idToken": id_token,
            "expiresAt": expires_at,
            "userId": payload.get("sub") if isinstance(payload.get("sub"), str) else None,
            "accountId": payload.get("https://api.openai.com/account_id") if isinstance(payload.get("https://api.openai.com/account_id"), str) else None,
            "planType": "free",
        }

    def validate_email_otp(
        self,
        client: OpenAiAuthClient,
        device_id: str,
        code: str,
        progress: OpenAiAuthProgress | None = None,
    ) -> dict[str, str]:
        """向 OpenAI 校验邮箱 OTP。"""

        log_progress(progress, "otp_validate_start")
        response = client.request(f"{AUTH_BASE}/api/accounts/email-otp/validate", {
            "method": "POST",
            "body": {"code": code},
            "referer": f"{AUTH_BASE}/email-verification",
            "headers": {"oai-device-id": device_id},
        })
        if response.status_code != 200:
            log_progress(progress, "otp_validate_retry_with_sentinel", {"status": response.status_code}, "warn")
            response = client.request(f"{AUTH_BASE}/api/accounts/email-otp/validate", {
                "method": "POST",
                "body": {"code": code},
                "referer": f"{AUTH_BASE}/email-verification",
                "headers": {
                    "oai-device-id": device_id,
                    "openai-sentinel-token": self.build_sentinel_token(client, device_id, "authorize_continue"),
                },
            })
        body = json_or_none(response.text)
        if response.status_code != 200:
            raise RuntimeError(f"OpenAI 验证码校验失败：{body_error(body, response.reason_phrase or f'HTTP {response.status_code}')}")
        next_url = continue_url(body)
        next_page_type = page_type(body)
        log_progress(progress, "otp_validate_success", {"pageType": next_page_type, "hasContinueUrl": bool(next_url)})
        return {"nextUrl": next_url, "pageType": next_page_type}

    def read_latest_verification_code(
        self,
        mailbox: dict[str, Any],
        target_email: str,
        since_ms: int,
        ignored_provider_mail_ids: set[str],
        progress: OpenAiAuthProgress | None = None,
    ) -> VerificationCodeCandidate | None:
        """读取最新可用 OpenAI 邮箱验证码候选。"""

        connection_id = mailbox.get("connection_id")
        if hasattr(self.mail_client, "list_inbox_messages"):
            summaries = self.mail_client.list_inbox_messages(mailbox["email"], OTP_MAIL_SCAN_LIMIT, connection_id)
        else:
            summaries = [
                {"id": provider_mail_id}
                for provider_mail_id in self.mail_client.list_inbox_message_ids(mailbox["email"], OTP_MAIL_SCAN_LIMIT, connection_id)
            ]
        threshold_ms = ((since_ms - OTP_MAIL_TIME_GRACE_MS) // 1000) * 1000
        for summary in summaries:
            provider_mail_id = mail_summary_id(summary)
            if not provider_mail_id:
                continue
            if provider_mail_id in ignored_provider_mail_ids:
                continue
            summary_time = mail_summary_time(summary)
            if summary_time is not None and summary_time < threshold_ms:
                log_progress(progress, "otp_scan_stopped_old", {
                    "providerMailId": provider_mail_id,
                    "dateMs": summary_time,
                    "sinceMs": since_ms,
                })
                break
            if mail_summary_unread(summary) is False:
                continue

            local_mail = self.mail_repo.get_mail_by_provider_id(mailbox["email"], provider_mail_id, connection_id)
            if local_mail and any(local_mail.get(key) for key in ("raw_json", "text", "html")):
                mail = cached_mail_to_openai_mail(local_mail)
            else:
                mail = self.mail_client.read_mail(mailbox["email"], provider_mail_id, connection_id)
                self.mail_repo.save_mail(mail_to_repository_input(mailbox["email"], mail, connection_id))

            parsed_mail_time = parse_mail_time(mail.get("date"))
            mail_time = parsed_mail_time or summary_time or 0
            if parsed_mail_time is not None and mail_time < threshold_ms:
                log_progress(progress, "otp_scan_stopped_old", {
                    "providerMailId": provider_mail_id,
                    "subject": mail.get("subject") or "",
                    "date": mail.get("date") or "",
                    "sinceMs": since_ms,
                })
                break
            code = extract_verification_code({
                "subject": mail.get("subject"),
                "text": mail.get("text", {}).get("content") if isinstance(mail.get("text"), dict) else None,
                "html": mail.get("html", {}).get("content") if isinstance(mail.get("html"), dict) else None,
            })
            if not code:
                continue
            if not mail_targets_expected_address(mail, target_email, mailbox["email"]):
                log_progress(progress, "otp_candidate_skipped_recipient", {
                    "providerMailId": provider_mail_id,
                    "subject": mail.get("subject") or "",
                    "date": mail.get("date") or "",
                })
                continue
            selected = VerificationCodeCandidate(code, provider_mail_id, mail_time or since_ms - OTP_MAIL_FALLBACK_ACCEPT_MS)
            log_progress(progress, "otp_candidate_seen", {
                "providerMailId": provider_mail_id,
                "subject": mail.get("subject") or "",
                "date": mail.get("date") or "",
                "codeSuffix": code[-2:],
            })
            # 收件箱列表已按时间倒序返回；找到第一个有效验证码后立即返回，避免继续读取旧邮件导致整轮轮询被网络超时拖失败。
            self.mail_client.read_mail(mailbox["email"], selected.provider_mail_id, connection_id, mark_read=True)
            local_mail = self.mail_repo.get_mail_by_provider_id(mailbox["email"], selected.provider_mail_id, connection_id)
            if local_mail:
                self.mail_repo.mark_mail_read(local_mail["id"])
            log_progress(progress, "otp_mail_marked_read", {
                "providerMailId": selected.provider_mail_id,
                "localMailId": local_mail.get("id") if local_mail else None,
            })
            return selected
        return None

    def wait_for_verification_code(
        self,
        mailbox: dict[str, Any],
        target_email: str,
        since_ms: int,
        timeout_ms: int,
        ignored_provider_mail_ids: set[str],
        progress: OpenAiAuthProgress | None = None,
    ) -> VerificationCodeCandidate:
        """等待 OpenAI 邮箱验证码。"""

        deadline = time.time() + timeout_ms / 1000
        fast_poll_until = time.time() + OTP_MAIL_FAST_POLL_WINDOW_MS / 1000
        last_log_at = 0.0
        last_error: Exception | None = None
        log_progress(progress, "otp_wait_start", {"mailboxEmail": mailbox["email"], "sinceMs": since_ms, "timeoutMs": timeout_ms})
        while time.time() < deadline:
            loop_started_at = time.time()
            try:
                candidate = self.read_latest_verification_code(mailbox, target_email, since_ms, ignored_provider_mail_ids, progress)
                if candidate:
                    log_progress(progress, "otp_found", {
                        "mailboxEmail": mailbox["email"],
                        "providerMailId": candidate.provider_mail_id,
                        "codeSuffix": candidate.code[-2:],
                    })
                    return candidate
            except Exception as exc:
                last_error = exc
                log_progress(progress, "otp_poll_failed", {
                    "mailboxEmail": mailbox["email"],
                    "error": str(exc),
                    "remainingMs": max(0, round((deadline - time.time()) * 1000)),
                }, "warn")
            if time.time() - last_log_at > 15:
                last_log_at = time.time()
                log_progress(progress, "otp_waiting", {"remainingMs": max(0, round((deadline - time.time()) * 1000))})
            poll_interval = (
                OTP_MAIL_FAST_POLL_INTERVAL_SECONDS
                if loop_started_at < fast_poll_until
                else OTP_MAIL_SLOW_POLL_INTERVAL_SECONDS
            )
            time.sleep(min(poll_interval, max(0, deadline - time.time())))
        if last_error:
            raise RuntimeError(f"等待 OpenAI 邮箱验证码超时；最后一次拉取邮箱失败：{last_error}")
        raise RuntimeError("等待 OpenAI 邮箱验证码超时")

    def wait_and_validate_email_otp(
        self,
        client: OpenAiAuthClient,
        device_id: str,
        mailbox: dict[str, Any],
        target_email: str,
        since_ms: int,
        timeout_ms: int,
        progress: OpenAiAuthProgress | None = None,
    ) -> dict[str, str]:
        """等待邮箱验证码并提交校验，失败后继续尝试下一封新邮件。"""

        ignored_provider_mail_ids: set[str] = set()
        deadline = time.time() + timeout_ms / 1000
        last_error: Exception | None = None
        while time.time() < deadline:
            candidate = self.wait_for_verification_code(
                mailbox,
                target_email,
                since_ms,
                max(1000, round((deadline - time.time()) * 1000)),
                ignored_provider_mail_ids,
                progress,
            )
            try:
                return self.validate_email_otp(client, device_id, candidate.code, progress)
            except Exception as exc:
                last_error = exc
                ignored_provider_mail_ids.add(candidate.provider_mail_id)
                log_progress(progress, "otp_validate_failed_try_next", {
                    "providerMailId": candidate.provider_mail_id,
                    "codeSuffix": candidate.code[-2:],
                    "remainingMs": max(0, round((deadline - time.time()) * 1000)),
                    "error": str(exc),
                }, "warn")
        raise RuntimeError(f"OpenAI 验证码校验失败，已尝试所有新验证码；最后错误：{last_error}")

    def extract_callback_params(
        self,
        client: OpenAiAuthClient,
        continue_url_value: str,
        device_id: str,
        progress: OpenAiAuthProgress | None = None,
        fallback_on_phone_step: bool = False,
        log_prefix: str = "oauth",
    ) -> dict[str, str] | None:
        """沿 OpenAI 授权跳转提取 OAuth callback code。"""

        direct_params = callback_params_from_url(continue_url_value)
        if direct_params:
            log_progress(progress, f"{log_prefix}_callback_direct", safe_url_summary(continue_url_value))
            return direct_params
        if fallback_on_phone_step and contains_phone_requirement(continue_url_value):
            raise RuntimeError(f"Sub2 授权登录遇到 add-phone 步骤：{phone_requirement_hint(continue_url_value) or 'continueUrl'}")
        current = urljoin(AUTH_BASE, continue_url_value or "/sign-in-with-chatgpt/codex/consent")
        for index in range(12):
            response = client.request(current, {"referer": AUTH_BASE, "redirectLimit": 0})
            location = response.headers.get("location")
            body = response.text
            body_json = json_or_none(body)
            response_continue = continue_url(body_json)
            response_continue_absolute = urljoin(current, response_continue) if response_continue else ""
            final_url = str(response.url) if response.url else ""
            log_progress(progress, f"{log_prefix}_redirect_step", {
                "index": index,
                "status": response.status_code,
                "current": safe_url_summary(current),
                "location": safe_url_summary(location),
                "finalUrl": safe_url_summary(final_url),
                "bodyContinueUrl": safe_url_summary(response_continue_absolute),
                "contentType": response.headers.get("content-type", ""),
                "bodyHint": re.sub(r"\s+", " ", body[:160]),
            })
            phone_hint = (
                phone_requirement_hint(current)
                or phone_requirement_hint(location or "")
                or phone_requirement_hint(response_continue_absolute)
                or phone_requirement_hint(body)
            )
            if fallback_on_phone_step and phone_hint:
                log_progress(progress, f"{log_prefix}_phone_step_detected", {"index": index, "hint": phone_hint}, "warn")
                raise RuntimeError(f"Sub2 授权登录遇到 add-phone 步骤：{phone_hint}")
            params = (
                callback_params_from_url(final_url)
                or (callback_params_from_url(urljoin(current, location)) if location else None)
                or (callback_params_from_url(response_continue_absolute) if response_continue_absolute else None)
            )
            if params:
                log_progress(progress, f"{log_prefix}_callback_from_redirect", {"index": index})
                return params
            next_url = urljoin(current, location) if location else response_continue_absolute
            if not next_url or (not response_continue_absolute and (response.status_code < 300 or response.status_code >= 400)):
                log_progress(progress, f"{log_prefix}_redirect_stopped", {
                    "index": index,
                    "status": response.status_code,
                    "hasLocation": bool(location),
                    "hasBodyContinueUrl": bool(response_continue_absolute),
                }, "warn")
                break
            current = next_url

        workspace_id = first_workspace_id(client.cookie("oai-client-auth-session"))
        if not workspace_id:
            log_progress(progress, f"{log_prefix}_callback_failed_no_workspace", {}, "warn")
            return None
        log_progress(progress, f"{log_prefix}_workspace_select_start", {"workspaceId": workspace_id})
        workspace = client.request(f"{AUTH_BASE}/api/accounts/workspace/select", {
            "method": "POST",
            "body": {"workspace_id": workspace_id},
            "referer": current,
            "headers": {"oai-device-id": device_id},
        })
        workspace_location = workspace.headers.get("location")
        log_progress(progress, f"{log_prefix}_workspace_select_response", {
            "status": workspace.status_code,
            "location": safe_url_summary(workspace_location),
        })
        if workspace_location:
            params = callback_params_from_url(urljoin(AUTH_BASE, workspace_location))
            if params:
                log_progress(progress, f"{log_prefix}_callback_from_workspace")
                return params
        workspace_body = json_or_none(workspace.text)
        root = as_record(workspace_body)
        data = as_record(root.get("data"))
        orgs = data.get("orgs") if isinstance(data.get("orgs"), list) else []
        first_org = orgs[0] if orgs else None
        if not isinstance(first_org, dict):
            log_progress(progress, f"{log_prefix}_callback_failed_no_org", {"workspaceKeys": list(root.keys())[:12]}, "warn")
            return None
        org_id = string_field(first_org, "id")
        projects = first_org.get("projects") if isinstance(first_org.get("projects"), list) else []
        first_project = projects[0] if projects else None
        project_id = string_field(first_project, "id") if isinstance(first_project, dict) else ""
        if not org_id:
            log_progress(progress, f"{log_prefix}_callback_failed_no_org_id", {}, "warn")
            return None
        log_progress(progress, f"{log_prefix}_organization_select_start", {"orgId": org_id, "projectId": project_id})
        organization = client.request(f"{AUTH_BASE}/api/accounts/organization/select", {
            "method": "POST",
            "body": {"org_id": org_id, **({"project_id": project_id} if project_id else {})},
            "referer": current,
            "headers": {"oai-device-id": device_id},
        })
        organization_location = organization.headers.get("location")
        log_progress(progress, f"{log_prefix}_organization_select_response", {
            "status": organization.status_code,
            "location": safe_url_summary(organization_location),
        })
        organization_params = callback_params_from_url(urljoin(AUTH_BASE, organization_location)) if organization_location else None
        if organization_params:
            log_progress(progress, f"{log_prefix}_callback_from_organization")
            return organization_params
        log_progress(progress, f"{log_prefix}_callback_failed_no_code", {}, "warn")
        return None

    def login_with_email_otp(self, duck_address: dict[str, Any], target_email: str, inbox_mailbox: dict[str, Any]) -> OpenAiLoginResult:
        """使用 Duck 邮箱完成 OpenAI 自动登录或注册。"""

        progress = OpenAiAuthProgress(
            operation_id=create_operation_id(),
            started_at=time.time(),
            email=target_email,
            inbox_email=inbox_mailbox["email"],
        )
        network = self.network_service.get()
        client = self.create_client()
        device_id = str(uuid.uuid4())
        pkce = generate_pkce()
        self.assert_chatgpt_region_available(client, progress)
        log_progress(progress, "authorize_start")
        authorize_url = f"{AUTH_BASE}/api/accounts/authorize?{urlencode({
            'issuer': AUTH_BASE,
            'client_id': PLATFORM_CLIENT_ID,
            'audience': PLATFORM_AUDIENCE,
            'redirect_uri': PLATFORM_REDIRECT_URI,
            'device_id': device_id,
            'screen_hint': 'login_or_signup',
            'max_age': '0',
            'login_hint': target_email,
            'scope': 'openid profile email offline_access',
            'response_type': 'code',
            'response_mode': 'query',
            'state': base64_url(random.randbytes(24)),
            'nonce': base64_url(random.randbytes(24)),
            'code_challenge': pkce['challenge'],
            'code_challenge_method': 'S256',
            'auth0Client': PLATFORM_AUTH0_CLIENT,
        })}"
        authorize = client.request(authorize_url, {"referer": f"{PLATFORM_BASE}/", "redirectLimit": 8})
        authorize_body = json_or_none(authorize.text)
        if authorize.status_code >= 400:
            raise RuntimeError(f"OpenAI authorize 失败：{body_error(authorize_body, authorize.reason_phrase or f'HTTP {authorize.status_code}')}")
        auth_continue_url = continue_url(authorize_body)
        auth_page_type = page_type(authorize_body)
        log_progress(progress, "authorize_success", {"status": authorize.status_code, **summarize_auth_body(authorize_body)})

        if auth_page_type == "create_account_password":
            self.register_password(client, device_id, target_email, random_password(), duck_address["id"], progress)
        elif auth_page_type == "login_password":
            password = str(duck_address.get("openai_password") or "").strip()
            if not password:
                raise RuntimeError("该 Duck 邮箱已注册 OpenAI 账号，但本地没有保存密码；请换一个新的 Duck 邮箱重新推送，或手动找回密码后再处理")
            password_result = self.verify_password(client, device_id, target_email, password, progress)
            next_url = password_result.next_url
            if requires_account_profile(password_result.page_type):
                next_url = self.create_account_profile(client, device_id, progress)
            elif password_result.requires_email_otp:
                validated = self.wait_and_validate_email_otp(
                    client,
                    device_id,
                    inbox_mailbox,
                    target_email,
                    password_result.otp_requested_at_ms or round(time.time() * 1000),
                    network.open_ai_otp_timeout_ms,
                    progress,
                )
                next_url = self.create_account_profile(client, device_id, progress) if requires_account_profile(validated["pageType"]) else validated["nextUrl"] or next_url
            callback = self.extract_callback_params(client, next_url, device_id, progress)
            if not callback:
                raise RuntimeError("OpenAI 密码登录成功后未拿到 OAuth callback code")
            log_progress(progress, "oauth_callback_success")
            token = self.exchange_tokens(client, callback["code"], pkce["verifier"])
            token["email"] = token.get("email") or normalize_email(target_email)
            self.duck_repo.update_openai_credentials(duck_address["id"], {"auth_json": serialize_openai_auth_json(token)})
            return OpenAiLoginResult(token=token, client=client, device_id=device_id)
        elif auth_page_type and re.search(r"email|otp|verification|login|signup|identifier", auth_page_type, re.I) is None:
            raise RuntimeError(f"OpenAI authorize 当前步骤不支持自动邮箱验证码：{auth_page_type}")

        if auth_continue_url:
            log_progress(progress, "authorize_continue_open", {"continueUrl": auth_continue_url})
            continued = client.request(urljoin(AUTH_BASE, auth_continue_url), {"referer": authorize_url, "redirectLimit": 4})
            continued_body = json_or_none(continued.text)
            log_progress(progress, "authorize_continue_success", {"status": continued.status_code, **summarize_auth_body(continued_body)})

        otp_requested_at_ms = round(time.time() * 1000)
        self.send_email_otp(
            client,
            device_id,
            urljoin(AUTH_BASE, auth_continue_url) if auth_continue_url else f"{AUTH_BASE}/email-verification",
            progress,
        )
        validated = self.wait_and_validate_email_otp(
            client,
            device_id,
            inbox_mailbox,
            target_email,
            otp_requested_at_ms,
            network.open_ai_otp_timeout_ms,
            progress,
        )
        next_url = self.create_account_profile(client, device_id, progress) if requires_account_profile(validated["pageType"]) else validated["nextUrl"]
        callback = self.extract_callback_params(client, next_url or validated["nextUrl"], device_id, progress)
        if not callback:
            raise RuntimeError("OpenAI 登录成功后未拿到 OAuth callback code")
        log_progress(progress, "oauth_callback_success")
        token = self.exchange_tokens(client, callback["code"], pkce["verifier"])
        token["email"] = token.get("email") or normalize_email(target_email)
        if normalize_email(token["email"]) != normalize_email(target_email):
            raise RuntimeError(f"OpenAI 登录账号 {token['email']} 与目标 Duck 邮箱 {target_email} 不一致")
        self.duck_repo.update_openai_credentials(duck_address["id"], {"auth_json": serialize_openai_auth_json(token)})
        log_progress(progress, "token_exchange_success", {"tokenEmail": token["email"]})
        return OpenAiLoginResult(token=token, client=client, device_id=device_id)


openai_auth_service = OpenAiAuthService()
