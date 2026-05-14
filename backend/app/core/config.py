from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LogLevel = Literal["trace", "debug", "info", "warn", "warning", "error", "fatal", "critical"]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """应用运行配置，优先读取环境变量，其次读取仓库根目录的 .env。"""

    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    app_name: str = "ClawEmail API"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: LogLevel = "info"
    database_url: str = Field(default=f"sqlite:///{(PROJECT_ROOT / 'data' / 'app.db').as_posix()}")
    database_path: str = ""
    admin_password: str = "admin@123456"
    claw_api_key: str = ""
    claw_dashboard_cookie: str = ""
    claw_workspace_id: str = ""
    claw_parent_mailbox_id: str = ""
    claw_root_prefix: str = ""
    claw_domain: str = "claw.163.com"
    system_proxy_url: str = ""
    system_request_timeout_ms: int = 10000
    sub2_api_url: str = ""
    sub2_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    sub2_proxy_template_json: str = ""

    @model_validator(mode="after")
    def apply_legacy_database_path(self):
        """兼容旧环境变量 DATABASE_PATH 并转换为 sqlite:/// 地址。"""

        if self.database_path and self.database_url == f"sqlite:///{(PROJECT_ROOT / 'data' / 'app.db').as_posix()}":
            path = Path(self.database_path)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            self.database_url = f"sqlite:///{path.as_posix()}"
        return self


@lru_cache
def get_settings() -> Settings:
    """读取并缓存应用配置。"""

    return Settings()
