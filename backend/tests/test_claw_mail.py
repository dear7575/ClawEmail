import importlib
from collections import deque

import httpx


class RecordingClient:
    def __init__(self, responses: deque[httpx.Response], calls: list[dict], **kwargs) -> None:
        self.responses = responses
        self.calls = calls
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def post(self, url: str, **kwargs):
        self.calls.append({"method": "POST", "url": url, "kwargs": kwargs, "client_kwargs": self.kwargs})
        return self.responses.popleft()

    def get(self, url: str, **kwargs):
        self.calls.append({"method": "GET", "url": url, "kwargs": kwargs, "client_kwargs": self.kwargs})
        return self.responses.popleft()


def reset_claw_mail_client(tmp_path, monkeypatch, responses: list[httpx.Response]):
    database_url = f"sqlite:///{tmp_path / 'claw-mail.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CLAW_API_KEY", "")
    import app.core.config as config_module
    import app.db.settings_repository as settings_repository_module
    import app.db.claw_repository as claw_repository_module
    import app.services.network_settings as network_settings_module
    import app.services.claw_mail as claw_mail_module

    config_module.get_settings.cache_clear()
    importlib.reload(settings_repository_module)
    importlib.reload(claw_repository_module)
    importlib.reload(network_settings_module)
    importlib.reload(claw_mail_module)
    settings_repository_module.settings_repository.set("claw.apiKey", "api-key")
    calls: list[dict] = []
    response_queue = deque(responses)
    client = claw_mail_module.ClawMailClient(
        repository=claw_repository_module.claw_repository,
        network_service=network_settings_module.network_settings_service,
        client_factory=lambda **kwargs: RecordingClient(response_queue, calls, **kwargs),
    )
    return client, calls


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"result": {"accessToken": "access-token", "expiresIn": 3600}})


def ok_response(value) -> httpx.Response:
    return httpx.Response(200, json={"code": "S_OK", "var": value})


def test_claw_mail_lists_inbox_message_ids_via_coremail_proxy(tmp_path, monkeypatch) -> None:
    client, calls = reset_claw_mail_client(tmp_path, monkeypatch, [
        token_response(),
        ok_response([{"id": "m1"}, {"id": "m2"}]),
    ])

    ids = client.list_inbox_message_ids("demo@claw.163.com", max_messages=10)

    assert ids == ["m1", "m2"]
    assert calls[0]["url"] == "https://claw.163.com/claw-api-gateway/open/v1/mail/auth/token"
    assert calls[0]["kwargs"]["headers"]["authorization"] == "Bearer api-key"
    assert calls[1]["url"] == "https://claw.163.com/claw-api-gateway/api/coremail/proxy"
    assert calls[1]["kwargs"]["params"] == {
        "uid": "demo@claw.163.com",
        "func": "mbox:listMessages",
    }
    assert calls[1]["kwargs"]["json"]["fid"] == 1


def test_claw_mail_send_uses_compose_continue_then_deliver(tmp_path, monkeypatch) -> None:
    client, calls = reset_claw_mail_client(tmp_path, monkeypatch, [
        token_response(),
        ok_response("compose-1"),
        ok_response(None),
    ])

    result = client.send_mail("demo@claw.163.com", {
        "to": ["target@example.com"],
        "subject": "hello",
        "body": "body",
        "html": False,
    })

    assert result == {"status": "sent"}
    assert calls[1]["kwargs"]["params"]["func"] == "mbox:compose"
    assert calls[1]["kwargs"]["json"]["action"] == "continue"
    assert calls[2]["kwargs"]["json"]["id"] == "compose-1"
    assert calls[2]["kwargs"]["json"]["action"] == "deliver"
