from typing import Literal

from pydantic import BaseModel

from app.db.settings_repository import SettingsRepository, settings_repository


ListenerLogMode = Literal["quiet", "lifecycle", "verbose"]
ListenerReconnectMode = Literal["standard", "slow"]
LOG_MODE_KEY = "listener.logMode"
RECONNECT_MODE_KEY = "listener.reconnectMode"


class ListenerSettings(BaseModel):
    """监听器运行偏好设置。"""

    logMode: ListenerLogMode = "quiet"
    reconnectMode: ListenerReconnectMode = "standard"


class ListenerSettingsUpdate(BaseModel):
    """监听器运行偏好更新请求。"""

    logMode: ListenerLogMode | None = None
    reconnectMode: ListenerReconnectMode | None = None


def normalize_listener_settings(input_value: dict | None = None) -> ListenerSettings:
    """归一化监听器设置，非法值回退到安全默认值。"""

    value = input_value or {}
    log_mode = value.get("logMode")
    reconnect_mode = value.get("reconnectMode")
    return ListenerSettings(
        logMode=log_mode if log_mode in {"lifecycle", "verbose"} else "quiet",
        reconnectMode="slow" if reconnect_mode == "slow" else "standard",
    )


class ListenerSettingsService:
    """监听器运行偏好设置，迁移期间只读写 SQLite 配置。"""

    def __init__(self, repository: SettingsRepository = settings_repository) -> None:
        """初始化监听器设置服务。"""

        self.repository = repository

    def get(self) -> ListenerSettings:
        """读取监听器设置。"""

        return normalize_listener_settings({
            "logMode": self.repository.get(LOG_MODE_KEY),
            "reconnectMode": self.repository.get(RECONNECT_MODE_KEY),
        })

    def save(self, update: ListenerSettingsUpdate) -> ListenerSettings:
        """保存监听器设置。"""

        settings = normalize_listener_settings(update.model_dump(exclude_none=True))
        self.repository.set(LOG_MODE_KEY, settings.logMode)
        self.repository.set(RECONNECT_MODE_KEY, settings.reconnectMode)
        return settings


listener_settings_service = ListenerSettingsService()
