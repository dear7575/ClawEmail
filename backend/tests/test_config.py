from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings


def test_settings_uses_legacy_database_path_when_database_url_is_default(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(database_path="./app/data/app.db")

    assert settings.database_url == f"sqlite:///{(PROJECT_ROOT / 'app' / 'data' / 'app.db').as_posix()}"


def test_settings_keeps_explicit_database_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(database_url="sqlite:///custom.db", database_path="./app/data/app.db")

    assert settings.database_url == "sqlite:///custom.db"
