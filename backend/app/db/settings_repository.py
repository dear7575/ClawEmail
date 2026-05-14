from pathlib import Path
from sqlite3 import Connection
from typing import Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import get_settings


def _sqlite_path(database_url: str) -> Path:
    """从 sqlite:/// 数据库 URL 中提取本地文件路径。"""

    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("当前迁移阶段只支持 sqlite:/// 数据库地址")
    return Path(database_url.removeprefix(prefix))


def create_settings_engine(database_url: str | None = None) -> Engine:
    """创建 SQLite SQLAlchemy Engine，并确保父目录存在。"""

    url = database_url or get_settings().database_url
    path = _sqlite_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args={"check_same_thread": False})


def ensure_app_settings_table(engine: Engine) -> None:
    """创建应用设置表。"""

    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))


class SettingsRepository:
    """读写旧版 SQLite app_settings 表，保持接口迁移期间的数据兼容。"""

    def __init__(self, engine: Engine | None = None) -> None:
        """初始化设置仓储并确保表结构存在。"""

        self.engine = engine or create_settings_engine()
        ensure_app_settings_table(self.engine)

    def get(self, key: str) -> str | None:
        """读取单个配置值。"""

        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT value FROM app_settings WHERE key = :key"),
                {"key": key}
            ).mappings().first()
        return str(row["value"]) if row else None

    def get_first(self, keys: Iterable[str]) -> str | None:
        """按顺序读取第一个存在的配置值。"""

        for key in keys:
            value = self.get(key)
            if value is not None:
                return value
        return None

    def set(self, key: str, value: str) -> None:
        """写入或更新配置值。"""

        with self.engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES (:key, :value, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                      value = excluded.value,
                      updated_at = CURRENT_TIMESTAMP
                """),
                {"key": key, "value": value}
            )

    def delete_many(self, keys: Iterable[str]) -> None:
        """批量删除配置键。"""

        with self.engine.begin() as connection:
            for key in keys:
                connection.execute(text("DELETE FROM app_settings WHERE key = :key"), {"key": key})


settings_repository = SettingsRepository()
