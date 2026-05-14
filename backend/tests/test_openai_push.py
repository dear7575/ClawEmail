import importlib
import json
from collections import deque

import httpx


class RecordingClient:
    def __init__(self, responses: list[httpx.Response], calls: list[dict], **kwargs) -> None:
        self.responses = deque(responses)
        self.calls = calls
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def request(self, method: str, url: str, headers: dict[str, str], json=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json, "kwargs": self.kwargs})
        return self.responses.popleft()


def reset_openai_push_service(tmp_path, monkeypatch, responses: list[httpx.Response] | None = None):
    database_url = f"sqlite:///{tmp_path / 'openai-push.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    import app.core.config as config_module
    import app.db.duck_repository as duck_repository_module
    import app.db.settings_repository as settings_repository_module
    import app.services.network_settings as network_settings_module
    import app.services.sub2 as sub2_module
    import app.services.duck as duck_service_module
    import app.services.openai_push as openai_push_module

    config_module.get_settings.cache_clear()
    importlib.reload(settings_repository_module)
    importlib.reload(duck_repository_module)
    importlib.reload(network_settings_module)
    importlib.reload(sub2_module)
    importlib.reload(duck_service_module)
    importlib.reload(openai_push_module)
    import app.api.duck as duck_api_module
    import app.api.openai_push as openai_push_api_module
    import app.api.sub2 as sub2_api_module

    calls: list[dict] = []
    if responses is not None:
        sub2_module.sub2_service = sub2_module.Sub2Service(
            repository=settings_repository_module.settings_repository,
            client_factory=lambda **kwargs: RecordingClient(responses, calls, **kwargs),
        )
        openai_push_module.sub2_service = sub2_module.sub2_service
        sub2_api_module.sub2_service = sub2_module.sub2_service
    duck_api_module.duck_service = duck_service_module.duck_service
    openai_push_module.openai_push_service = openai_push_module.OpenAiPushService(duck_repository_module.duck_repository)
    openai_push_api_module.openai_push_service = openai_push_module.openai_push_service
    return duck_repository_module.duck_repository, settings_repository_module.settings_repository, calls


def create_duck_address_with_credentials(repository) -> int:
    repository.create_account("duck:1", "main", "token")
    address = repository.save_address({
        "account_id": "duck:1",
        "address": "private@duck.com",
        "local_part": "private",
        "forwarding_mailbox_email": "root@claw.163.com",
        "note": None,
        "raw_json": "{}",
    })
    repository.update_openai_credentials(address["id"], {
        "auth_json": json.dumps({
            "email": "private@duck.com",
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "expiresAt": "2026-05-13T12:00:00Z",
            "userId": "user-id",
            "accountId": "account-id",
            "planType": "free",
        }),
    })
    return address["id"]


def test_openai_duck_push_uses_saved_auth_json_and_sub2_proxy(tmp_path, monkeypatch, test_client) -> None:
    responses = [
        httpx.Response(200, json={"data": {"items": [{
            "id": 5,
            "name": "proxy-5",
            "protocol": "http",
            "host": "127.0.0.1",
            "port": 7890,
            "username": "",
            "password": "",
        }]}}),
        httpx.Response(200, json={"code": 0, "data": {"id": 100}}),
    ]
    repository, settings, calls = reset_openai_push_service(tmp_path, monkeypatch, responses)
    settings.set("sub2.apiUrl", "https://sub2.example.com")
    settings.set("sub2.apiKey", "adminkey")
    settings.set("sub2.defaultGroupId", "9")
    address_id = create_duck_address_with_credentials(repository)

    response = test_client.post("/api/openai/duck-push-sub2", json={"duckAddressId": address_id})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["email"] == "private@duck.com"
    assert body["pushMode"] == "oauth_token"
    assert body["data"]["accounts"][0]["credentials"]["access_token"] == "access-token"
    assert body["data"]["accounts"][0]["group_ids"] == [9]
    assert "授权登录成功" in body["data"]["accounts"][0]["notes"]
    assert "Duck 邮箱：private@duck.com" in body["data"]["accounts"][0]["notes"]
    assert calls[-1]["url"] == "https://sub2.example.com/api/v1/admin/accounts"
    assert calls[-1]["json"]["proxy_id"] == 5
    assert "授权登录成功" in calls[-1]["json"]["notes"]
    saved_address = repository.get_address(address_id)
    assert saved_address is not None
    assert saved_address["sub2_pushed_at"] is not None
    assert saved_address["sub2_push_mode"] == "oauth_token"
    assert saved_address["sub2_push_email"] == "private@duck.com"


def test_openai_duck_push_requires_saved_auth_json(tmp_path, monkeypatch, test_client) -> None:
    repository, _settings, _calls = reset_openai_push_service(tmp_path, monkeypatch)
    repository.create_account("duck:1", "main", "token")
    address = repository.save_address({
        "account_id": "duck:1",
        "address": "private@duck.com",
        "local_part": "private",
        "forwarding_mailbox_email": "root@claw.163.com",
        "note": None,
        "raw_json": "{}",
    })

    response = test_client.post("/api/openai/duck-push-sub2", json={"duckAddressId": address["id"]})

    assert response.status_code == 500
    assert "还没有保存 OpenAI 授权信息" in response.json()["error"]
