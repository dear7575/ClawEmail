from pydantic import AnyUrl, BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_settings
from app.db.settings_repository import SettingsRepository, settings_repository


PROXY_URL_KEY = "system.proxyUrl"
TIMEOUT_MS_KEY = "system.timeoutMs"
OPENAI_OTP_TIMEOUT_MS_KEY = "openai.otpTimeoutMs"
LEGACY_DUCK_PROXY_URL_KEY = "duck.proxyUrl"
LEGACY_DUCK_TIMEOUT_MS_KEY = "duck.timeoutMs"
DEFAULT_TIMEOUT_MS = 10000
DEFAULT_OPENAI_OTP_TIMEOUT_MS = 60000
MIN_TIMEOUT_MS = 1000
MAX_TIMEOUT_MS = 120000
MIN_OPENAI_OTP_TIMEOUT_MS = 15000
MAX_OPENAI_OTP_TIMEOUT_MS = 300000


class NetworkSettings(BaseModel):
    """系统网络设置。"""

    proxy_url: str = Field(default="", alias="proxyUrl")
    timeout_ms: int = Field(default=DEFAULT_TIMEOUT_MS, alias="timeoutMs")
    open_ai_otp_timeout_ms: int = Field(default=DEFAULT_OPENAI_OTP_TIMEOUT_MS, alias="openAiOtpTimeoutMs")

    model_config = ConfigDict(populate_by_name=True)


class NetworkSettingsUpdate(BaseModel):
    """系统网络设置更新请求。"""

    proxy_url: str | None = Field(default=None, alias="proxyUrl", max_length=300)
    timeout_ms: int | None = Field(default=None, alias="timeoutMs", ge=MIN_TIMEOUT_MS, le=MAX_TIMEOUT_MS)
    open_ai_otp_timeout_ms: int | None = Field(
        default=None,
        alias="openAiOtpTimeoutMs",
        ge=MIN_OPENAI_OTP_TIMEOUT_MS,
        le=MAX_OPENAI_OTP_TIMEOUT_MS
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("proxy_url")
    @classmethod
    def normalize_proxy_url(cls, value: str | None) -> str | None:
        """校验请求体里的代理地址。"""

        if value is None:
            return None
        return normalize_proxy_url(value)


def normalize_timeout_ms(value: int | str | None) -> int:
    """归一化普通外部请求超时时间。"""

    settings = get_settings()
    raw = value if value is not None else settings.system_request_timeout_ms
    try:
        parsed = round(float(raw))
    except (TypeError, ValueError):
        parsed = DEFAULT_TIMEOUT_MS
    return min(MAX_TIMEOUT_MS, max(MIN_TIMEOUT_MS, parsed))


def normalize_openai_otp_timeout_ms(value: int | str | None) -> int:
    """归一化 OpenAI OTP 等待超时时间。"""

    try:
        parsed = round(float(value if value is not None else DEFAULT_OPENAI_OTP_TIMEOUT_MS))
    except (TypeError, ValueError):
        parsed = DEFAULT_OPENAI_OTP_TIMEOUT_MS
    return min(MAX_OPENAI_OTP_TIMEOUT_MS, max(MIN_OPENAI_OTP_TIMEOUT_MS, parsed))


def normalize_proxy_url(value: str | None) -> str:
    """校验并归一化系统 HTTP(S) 代理地址。"""

    trimmed = (value or "").strip()
    if not trimmed:
        return ""
    parsed = AnyUrl(trimmed)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("系统代理地址只支持 http:// 或 https://")
    return str(parsed)


class NetworkSettingsService:
    """系统网络设置读写服务。"""

    def __init__(self, repository: SettingsRepository = settings_repository) -> None:
        """初始化网络设置服务。"""

        self.repository = repository

    def get(self) -> NetworkSettings:
        """读取网络设置，兼容旧 Duck 网络设置键。"""

        settings = get_settings()
        proxy_url = self.repository.get_first([PROXY_URL_KEY, LEGACY_DUCK_PROXY_URL_KEY])
        timeout_ms = self.repository.get_first([TIMEOUT_MS_KEY, LEGACY_DUCK_TIMEOUT_MS_KEY])
        openai_timeout_ms = self.repository.get(OPENAI_OTP_TIMEOUT_MS_KEY)
        return NetworkSettings(
            proxyUrl=normalize_proxy_url(proxy_url or settings.system_proxy_url),
            timeoutMs=normalize_timeout_ms(timeout_ms),
            openAiOtpTimeoutMs=normalize_openai_otp_timeout_ms(openai_timeout_ms)
        )

    def save(self, update: NetworkSettingsUpdate) -> NetworkSettings:
        """保存网络设置。"""

        current = self.get()
        next_settings = NetworkSettings(
            proxyUrl=update.proxy_url if update.proxy_url is not None else "",
            timeoutMs=normalize_timeout_ms(update.timeout_ms),
            openAiOtpTimeoutMs=normalize_openai_otp_timeout_ms(
                update.open_ai_otp_timeout_ms if update.open_ai_otp_timeout_ms is not None else current.open_ai_otp_timeout_ms
            )
        )
        self.repository.set(PROXY_URL_KEY, next_settings.proxy_url)
        self.repository.set(TIMEOUT_MS_KEY, str(next_settings.timeout_ms))
        self.repository.set(OPENAI_OTP_TIMEOUT_MS_KEY, str(next_settings.open_ai_otp_timeout_ms))
        return next_settings


network_settings_service = NetworkSettingsService()
