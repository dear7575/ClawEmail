from app.services.inbucket import InbucketSettings, InbucketSettingsUpdate, InbucketSettingsService
from app.services.mails import MailService


class FakeInbucketClient:
    def __init__(self):
        self.detail_calls: list[tuple[str, str]] = []

    def read_message(self, mailbox: str, message_id: str) -> dict:
        self.detail_calls.append((mailbox, message_id))
        return {
            "id": message_id,
            "from": "sender@example.test",
            "to": [mailbox],
            "subject": "Order received",
            "date": "2026-11-04T07:00:00Z",
            "header": [{"name": "To", "value": f"<{mailbox}@example.test>"}],
            "body": {"text": "Hello", "html": "<p>Hello</p>"},
        }


class FakeRepository:
    def __init__(self):
        self.mails: dict[tuple[str, str], dict] = {}
        self.mailboxes: dict[str, dict] = {}

    def get_mail_by_provider_id(self, mailbox: str, provider_id: str, connection_id: str):
        return self.mails.get((mailbox, provider_id))

    def upsert_mailbox(self, mailbox: dict) -> dict:
        self.mailboxes[mailbox["email"]] = mailbox
        return mailbox

    def save_mail(self, mail: dict) -> dict:
        saved = {"id": len(self.mails) + 1, **mail}
        self.mails[(mail["mailbox_email"], mail["provider_mail_id"])] = saved
        return saved


class FakeAlertService:
    def __init__(self):
        self.mails: list[dict] = []

    def notify_new_mail(self, mail: dict) -> dict:
        self.mails.append(mail)
        return {"sent": False}


def test_inbucket_settings_normalize_url_and_exclude_password() -> None:
    settings = InbucketSettingsUpdate(
        baseUrl=" http://inbucket:9000/ ",
        username=" proxy-user ",
        password=" secret ",
    )

    assert settings.base_url == "http://inbucket:9000"
    assert settings.username == "proxy-user"
    assert settings.password == "secret"
    assert "secret" not in InbucketSettings(baseUrl=settings.base_url, username=settings.username, hasPassword=True).model_dump_json()


def test_inbucket_settings_reject_invalid_url() -> None:
    try:
        InbucketSettingsUpdate(baseUrl="inbucket:9000")
    except ValueError as exc:
        assert "http://" in str(exc)
    else:
        raise AssertionError("invalid Inbucket URL should be rejected")


def test_inbucket_settings_persist_credentials_without_returning_password() -> None:
    class FakeSettingsRepository:
        def __init__(self):
            self.values: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.values.get(key)

        def set(self, key: str, value: str) -> None:
            self.values[key] = value

    service = InbucketSettingsService(FakeSettingsRepository())
    saved = service.save(InbucketSettingsUpdate(
        baseUrl="http://inbucket:9000",
        username="proxy-user",
        password="proxy-secret",
    ))

    assert saved.has_password is True
    assert service.credentials() == ("proxy-user", "proxy-secret")
    assert "proxy-secret" not in saved.model_dump_json()


def test_receive_inbucket_event_saves_deduplicates_and_notifies(monkeypatch) -> None:
    repository = FakeRepository()
    client = FakeInbucketClient()
    alerts = FakeAlertService()
    events: list[tuple[str, dict]] = []
    service = MailService(
        repository=repository,
        alert_service=alerts,
        inbucket_client_value=client,
    )

    from app.services import sse
    monkeypatch.setattr(sse.sse_hub, "broadcast", lambda event, payload: events.append((event, payload)))

    message_event = {
        "variant": "message-stored",
        "identifier": {"mailbox": "orders", "id": "mail-1"},
        "header": {"subject": "Order received"},
    }
    service.receive_inbucket_event(message_event)
    service.receive_inbucket_event(message_event)

    saved = repository.mails[("orders", "inbucket:mail-1")]
    assert saved["connection_id"] == "inbucket"
    assert saved["subject"] == "Order received"
    assert saved["source"] == "sender@example.test"
    assert saved["address"] == "orders@example.test"
    assert saved["text"] == "Hello"
    assert saved["html"] == "<p>Hello</p>"
    assert client.detail_calls == [("orders", "mail-1")]
    assert "orders" in repository.mailboxes
    assert len(alerts.mails) == 1
    assert len(events) == 1


def test_inbucket_client_uses_basic_auth_for_rest_requests(monkeypatch) -> None:
    from app.services.inbucket import InbucketClient
    from app.services import inbucket as inbucket_module

    class FakeSettingsRepository:
        def __init__(self):
            self.values: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.values.get(key)

        def set(self, key: str, value: str) -> None:
            self.values[key] = value

    class FakeNetworkSettings:
        proxy_url = ""
        timeout_ms = 10000

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"id": "mail-1"}

    class FakeHttpClient:
        def __init__(self, **kwargs):
            calls["options"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url: str):
            calls["url"] = url
            return FakeResponse()

    calls: dict = {}
    settings_service = InbucketSettingsService(FakeSettingsRepository())
    settings_service.save(InbucketSettingsUpdate(
        baseUrl="https://mail.example.test",
        username="proxy-user",
        password="proxy-secret",
    ))
    monkeypatch.setattr(inbucket_module.network_settings_service, "get", lambda: FakeNetworkSettings())
    client = InbucketClient(settings_service, FakeHttpClient)

    assert client.read_message("orders", "mail-1") == {"id": "mail-1"}
    assert calls["options"]["auth"] == ("proxy-user", "proxy-secret")
    assert calls["url"] == "https://mail.example.test/api/v1/mailbox/orders/mail-1"


def test_inbucket_settings_api_persists_but_never_returns_password(test_client, monkeypatch) -> None:
    from app.services.inbucket import inbucket_settings_service

    class FakeSettingsRepository:
        def __init__(self):
            self.values: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.values.get(key)

        def set(self, key: str, value: str) -> None:
            self.values[key] = value

    monkeypatch.setattr(inbucket_settings_service, "repository", FakeSettingsRepository())
    response = test_client.put("/api/inbucket/settings", json={
        "baseUrl": "http://inbucket:9000/",
        "username": "proxy-user",
        "password": "proxy-secret",
    })

    assert response.status_code == 200
    assert response.json() == {
        "baseUrl": "http://inbucket:9000",
        "username": "proxy-user",
        "hasPassword": True,
    }
    assert "proxy-secret" not in response.text
    assert test_client.get("/api/inbucket/settings").json() == response.json()
