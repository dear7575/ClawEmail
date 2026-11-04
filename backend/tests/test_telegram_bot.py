from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.services.telegram_bot import TelegramBotService


class FakeSettingsRepository:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete_many(self, keys) -> None:
        for key in keys:
            self.values.pop(key, None)


class FakeMailRepository:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def list_recent_mail_summaries(self, limit: int, unread_only: bool) -> list[dict]:
        self.calls.append(unread_only)
        return [{
            "id": 1,
            "mailbox_email": "demo@example.com",
            "source": "sender@example.com",
            "subject": "<script>alert(1)</script>",
            "has_attachments": 1,
            "received_at": "2026-11-04",
        }]


class FakeTelegramService:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            enabled=True,
            bot_token="token",
            chat_id="123",
            webhook_base_url="https://mail.example.com",
        )
        self.calls: list[tuple[str, dict]] = []
        self.responses = [
            {"ok": True, "result": {"url": ""}},
            {"ok": True},
            {"ok": True},
        ]
        self.messages: list[tuple[str, str | None]] = []

    def get_settings(self):
        return self.settings

    def call_api(self, method: str, payload: dict) -> dict:
        self.calls.append((method, payload))
        return self.responses.pop(0)

    def send_message(self, text: str, parse_mode: str | None = None, **_kwargs) -> None:
        self.messages.append((text, parse_mode))


def test_install_registers_webhook_and_commands(monkeypatch) -> None:
    repository = FakeSettingsRepository()
    repository.set("telegram.webhookBaseUrl", "https://mail.example.com")
    telegram = FakeTelegramService()
    service = TelegramBotService(repository, FakeMailRepository(), telegram)

    result = service.install()

    assert result == {"installed": True}
    assert [call[0] for call in telegram.calls] == ["getWebhookInfo", "setMyCommands", "setWebhook"]
    webhook_payload = telegram.calls[2][1]
    assert webhook_payload["url"] == "https://mail.example.com/api/telegram/webhook"
    assert webhook_payload["secret_token"] == repository.get("telegram.webhookSecret")
    assert telegram.calls[1][1]["commands"][-1]["command"] == "unread"


def test_install_refuses_to_replace_another_webhook() -> None:
    repository = FakeSettingsRepository()
    repository.set("telegram.webhookBaseUrl", "https://mail.example.com")
    telegram = FakeTelegramService()
    telegram.responses[0] = {"ok": True, "result": {"url": "https://other.example/webhook"}}
    service = TelegramBotService(repository, FakeMailRepository(), telegram)

    try:
        service.install()
    except ValueError as error:
        assert "Webhook" in str(error)
    else:
        raise AssertionError("install should not replace another webhook")
    assert [call[0] for call in telegram.calls] == ["getWebhookInfo"]


def test_commands_reply_only_to_configured_chat_and_dedupe_updates() -> None:
    repository = FakeSettingsRepository()
    telegram = FakeTelegramService()
    mail_repository = FakeMailRepository()
    service = TelegramBotService(repository, mail_repository, telegram)

    service.handle_update({"update_id": 10, "message": {"text": "/unread", "chat": {"id": 123}}})
    service.handle_update({"update_id": 10, "message": {"text": "/unread", "chat": {"id": 123}}})
    service.handle_update({"update_id": 11, "message": {"text": "/latest", "chat": {"id": 999}}})

    assert len(telegram.messages) == 1
    assert telegram.messages[0][1] == "HTML"
    assert "&lt;script&gt;" in telegram.messages[0][0]
    assert "<script>" not in telegram.messages[0][0]
    assert mail_repository.calls == [True]
    assert repository.get("telegram.lastUpdateId") == "11"


def test_webhook_requires_both_secrets_without_admin_password(monkeypatch) -> None:
    import app.api.telegram as telegram_api_module
    from app.main import app

    repository = FakeSettingsRepository()
    repository.set("telegram.webhookPath", "path-secret")
    repository.set("telegram.webhookSecret", "header-secret")
    handled: list[dict] = []
    monkeypatch.setattr(telegram_api_module, "settings_repository", repository)
    monkeypatch.setattr(
        telegram_api_module,
        "telegram_bot_service",
        SimpleNamespace(handle_update=handled.append),
    )
    client = TestClient(app)

    denied = client.post(
        "/api/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    accepted = client.post(
        "/api/telegram/webhook",
        json={"update_id": 2},
        headers={"X-Telegram-Bot-Api-Secret-Token": "header-secret"},
    )

    assert denied.status_code == 403
    assert accepted.status_code == 200
    assert handled == [{"update_id": 2}]
