import importlib
from collections import deque

import httpx
from app.main import app


class RecordingClient:
    def __init__(self, responses: list[httpx.Response], calls: list[dict], **kwargs) -> None:
        self.responses = deque(responses)
        self.calls = calls
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def request(self, method: str, url: str, headers: dict[str, str], json: dict):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "json": json,
            "kwargs": self.kwargs,
        })
        return self.responses.popleft()


def reset_telegram_service(tmp_path, monkeypatch, responses: list[httpx.Response] | None = None):
    database_url = f"sqlite:///{tmp_path / 'telegram.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    import app.core.config as config_module
    import app.db.settings_repository as repository_module
    import app.services.network_settings as network_service_module
    import app.services.telegram as telegram_service_module

    config_module.get_settings.cache_clear()
    importlib.reload(repository_module)
    importlib.reload(network_service_module)
    importlib.reload(telegram_service_module)
    import app.api.system as system_module
    import app.api.telegram as telegram_api_module

    system_module.network_settings_service = network_service_module.network_settings_service
    calls: list[dict] = []
    if responses is not None:
        telegram_service_module.telegram_service = telegram_service_module.TelegramService(
            repository=repository_module.settings_repository,
            client_factory=lambda **kwargs: RecordingClient(responses, calls, **kwargs),
        )
        telegram_api_module.telegram_service = telegram_service_module.telegram_service
    else:
        telegram_api_module.telegram_service = telegram_service_module.telegram_service
    return repository_module.settings_repository, calls


def test_get_telegram_settings_returns_defaults(tmp_path, monkeypatch, test_client) -> None:
    reset_telegram_service(tmp_path, monkeypatch)
    client = test_client

    response = client.get("/api/telegram/settings")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "chatId": "",
        "hasBotToken": False,
        "botTokenPreview": None,
    }


def test_put_telegram_settings_persists_and_masks_token(tmp_path, monkeypatch, test_client) -> None:
    repository, _calls = reset_telegram_service(tmp_path, monkeypatch)
    client = test_client

    response = client.put("/api/telegram/settings", json={
        "enabled": True,
        "botToken": " 1234567890abcdef ",
        "chatId": " 1001 ",
    })

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "chatId": "1001",
        "hasBotToken": True,
        "botTokenPreview": "12345678...cdef",
    }
    assert repository.get("telegram.enabled") == "true"
    assert repository.get("telegram.botToken") == "1234567890abcdef"
    assert repository.get("telegram.chatId") == "1001"


def test_put_telegram_settings_preserves_token_when_omitted(tmp_path, monkeypatch, test_client) -> None:
    repository, _calls = reset_telegram_service(tmp_path, monkeypatch)
    repository.set("telegram.botToken", "secret-token")
    client = test_client

    response = client.put("/api/telegram/settings", json={
        "enabled": True,
        "chatId": "2002",
    })

    assert response.status_code == 200
    assert response.json()["botTokenPreview"] == "secr****"
    assert repository.get("telegram.botToken") == "secret-token"


def test_send_telegram_message_posts_to_telegram(tmp_path, monkeypatch, test_client) -> None:
    repository, calls = reset_telegram_service(tmp_path, monkeypatch, [httpx.Response(200, json={"ok": True})])
    repository.set("telegram.enabled", "true")
    repository.set("telegram.botToken", "token")
    repository.set("telegram.chatId", "chat")
    client = test_client

    response = client.post("/api/telegram/send", json={"text": " hello "})

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://api.telegram.org/bottoken/sendMessage"
    assert calls[0]["headers"] == {"content-type": "application/json"}
    assert calls[0]["json"] == {"chat_id": "chat", "text": "hello"}


def test_send_telegram_message_requires_enabled(tmp_path, monkeypatch, test_client) -> None:
    reset_telegram_service(tmp_path, monkeypatch)
    client = test_client

    response = client.post("/api/telegram/send", json={"text": "hello"})

    assert response.status_code == 500
    assert response.json()["error"] == "Telegram 消息通知未启用"
