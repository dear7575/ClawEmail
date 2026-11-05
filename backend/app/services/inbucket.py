import base64
import json
import logging
from collections.abc import Callable
from threading import Event, Thread
from urllib.parse import quote

import httpx
from pydantic import AnyUrl, BaseModel, ConfigDict, Field, field_validator
from websockets.sync.client import connect

from app.core.config import get_settings
from app.db.settings_repository import SettingsRepository
from app.services.network_settings import network_settings_service


logger = logging.getLogger(__name__)
BASE_URL_KEY = "inbucket.baseUrl"
USERNAME_KEY = "inbucket.username"
PASSWORD_KEY = "inbucket.password"
CONNECTION_ID = "inbucket"


class InbucketSettings(BaseModel):
    base_url: str = Field(default="", alias="baseUrl")
    username: str = ""
    has_password: bool = Field(default=False, alias="hasPassword")

    model_config = ConfigDict(populate_by_name=True)


class InbucketSettingsUpdate(BaseModel):
    base_url: str = Field(default="", alias="baseUrl", max_length=500)
    username: str = ""
    password: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return ""
        try:
            parsed = AnyUrl(value)
        except ValueError as exc:
            raise ValueError("Inbucket 地址必须是有效的 HTTP(S) 地址") from exc
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Inbucket 地址只支持 http:// 或 https://")
        return value

    @field_validator("username", "password")
    @classmethod
    def trim_credentials(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class InbucketSettingsService:
    def __init__(self, repository: SettingsRepository | None = None) -> None:
        self.repository = repository
        self._repository_is_injected = repository is not None
        self._database_url = get_settings().database_url if repository is None else None

    def _settings_repository(self) -> SettingsRepository:
        database_url = get_settings().database_url
        if self.repository is None or (not self._repository_is_injected and self._database_url != database_url):
            self.repository = SettingsRepository()
            self._database_url = database_url
        return self.repository

    def get(self) -> InbucketSettings:
        repository = self._settings_repository()
        return InbucketSettings(
            baseUrl=repository.get(BASE_URL_KEY) or "",
            username=repository.get(USERNAME_KEY) or "",
            hasPassword=bool(repository.get(PASSWORD_KEY)),
        )

    def credentials(self) -> tuple[str, str]:
        repository = self._settings_repository()
        return repository.get(USERNAME_KEY) or "", repository.get(PASSWORD_KEY) or ""

    def save(self, update: InbucketSettingsUpdate) -> InbucketSettings:
        repository = self._settings_repository()
        _, current_password = self.credentials()
        username = update.username
        password = update.password if update.password is not None else current_password
        repository.set(BASE_URL_KEY, update.base_url)
        repository.set(USERNAME_KEY, username)
        repository.set(PASSWORD_KEY, password)
        return InbucketSettings(baseUrl=update.base_url, username=username, hasPassword=bool(password))


class InbucketClient:
    def __init__(
        self,
        settings_service: InbucketSettingsService | None = None,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.settings_service = settings_service or inbucket_settings_service
        self.client_factory = client_factory

    def _client_options(self) -> dict:
        network = network_settings_service.get()
        credentials = self.settings_service.credentials()
        return {
            "timeout": network.timeout_ms / 1000,
            "proxy": network.proxy_url or None,
            "auth": credentials if any(credentials) else None,
        }

    def _get(self, path: str) -> object:
        settings = self.settings_service.get()
        if not settings.base_url:
            raise ValueError("请先配置 Inbucket 地址")
        with self.client_factory(**self._client_options()) as client:
            response = client.get(f"{settings.base_url}/api/v1/{path}")
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("Inbucket 返回了无效 JSON") from exc

    def delete_message(self, mailbox: str, message_id: str) -> None:
        settings = self.settings_service.get()
        if not settings.base_url:
            raise ValueError("请先配置 Inbucket 地址")
        with self.client_factory(**self._client_options()) as client:
            response = client.delete(
                f"{settings.base_url}/api/v1/mailbox/{quote(mailbox, safe='')}/{quote(message_id, safe='')}"
            )
        response.raise_for_status()

    def read_message(self, mailbox: str, message_id: str) -> dict:
        payload = self._get(f"mailbox/{quote(mailbox, safe='')}/{quote(message_id, safe='')}")
        if not isinstance(payload, dict):
            raise RuntimeError(f"Inbucket 邮件详情格式无效：{mailbox}/{message_id}")
        return payload


class InbucketMonitor:
    def __init__(self, on_message: Callable[[dict], None]) -> None:
        self.on_message = on_message
        self.stop_event = Event()
        self.wake_event = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = Thread(target=self._run, name="inbucket-monitor", daemon=True)
        self.thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)

    def reload_settings(self) -> None:
        self.wake_event.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            settings = inbucket_settings_service.get()
            if not settings.base_url:
                self.wake_event.wait(5)
                self.wake_event.clear()
                continue
            network = network_settings_service.get()
            username, password = inbucket_settings_service.credentials()
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            url = settings.base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
            try:
                with connect(
                    f"{url}/api/v2/monitor/messages",
                    additional_headers={"Authorization": f"Basic {token}"} if username or password else None,
                    proxy=network.proxy_url or None,
                    open_timeout=network.timeout_ms / 1000,
                    max_size=2**20,
                ) as socket:
                    while not self.stop_event.is_set() and not self.wake_event.is_set():
                        try:
                            raw_message = socket.recv(timeout=1)
                        except TimeoutError:
                            continue
                        message = json.loads(raw_message)
                        if isinstance(message, dict):
                            self.on_message(message)
            except Exception as exc:
                if not self.stop_event.is_set():
                    logger.warning("Inbucket 监控连接断开，将重连：%s", exc)
            self.wake_event.wait(3)
            self.wake_event.clear()


inbucket_settings_service = InbucketSettingsService()
inbucket_client = InbucketClient()
