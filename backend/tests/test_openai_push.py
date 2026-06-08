import importlib
import json
import time
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

    def request(self, method: str, url: str, headers: dict[str, str], json=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json, "kwargs": self.kwargs})
        return self.responses.popleft()


def reset_openai_push_service(tmp_path, monkeypatch, responses: list[httpx.Response] | None = None):
    database_url = f"sqlite:///{tmp_path / 'openai-push.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    import app.core.config as config_module
    import app.db.duck_repository as duck_repository_module
    import app.db.mail_repository as mail_repository_module
    import app.db.settings_repository as settings_repository_module
    import app.services.network_settings as network_settings_module
    import app.services.openai_auth as openai_auth_module
    import app.services.sub2 as sub2_module
    import app.services.duck as duck_service_module
    import app.services.openai_push as openai_push_module

    config_module.get_settings.cache_clear()
    importlib.reload(settings_repository_module)
    importlib.reload(duck_repository_module)
    importlib.reload(mail_repository_module)
    importlib.reload(network_settings_module)
    importlib.reload(openai_auth_module)
    importlib.reload(sub2_module)
    importlib.reload(duck_service_module)
    importlib.reload(openai_push_module)
    import app.api.duck as duck_api_module
    import app.api.openai_push as openai_push_api_module
    import app.api.sub2 as sub2_api_module

    calls: list[dict] = []
    if responses is not None:
        response_queue = deque(responses)
        sub2_module.sub2_service = sub2_module.Sub2Service(
            repository=settings_repository_module.settings_repository,
            client_factory=lambda **kwargs: RecordingClient(response_queue, calls, **kwargs),
        )
        openai_push_module.sub2_service = sub2_module.sub2_service
        sub2_api_module.sub2_service = sub2_module.sub2_service
    class FakeMailClient:
        def __init__(self) -> None:
            self.api_key_calls: list[str | None] = []

        def api_key(self, connection_id: str | None = None) -> str:
            self.api_key_calls.append(connection_id)
            return "fake-api-key"

    class FakeAuthService:
        def __init__(self) -> None:
            self.mail_client = FakeMailClient()
            self.login_calls: list[tuple[dict, str, dict]] = []
            self.extract_calls: list[dict] = []
            self.raise_phone_requirement = False

        def login_with_email_otp(self, duck_address: dict, target_email: str, inbox_mailbox: dict):
            self.login_calls.append((duck_address, target_email, inbox_mailbox))

            class FakeLoginResult:
                def __init__(self, token, client, device_id) -> None:
                    self.token = token
                    self.client = client
                    self.device_id = device_id

            token = {
                "email": target_email,
                "accessToken": "access-token",
                "refreshToken": "refresh-token",
                "idToken": "id-token",
                "expiresAt": "2026-05-13T12:00:00Z",
                "userId": "user-id",
                "accountId": "account-id",
                "planType": "free",
            }
            duck_repository_module.duck_repository.update_openai_credentials(
                duck_address["id"],
                {"auth_json": json.dumps(token)},
            )
            return FakeLoginResult(token, client=object(), device_id="device-id")

        def extract_callback_params(self, client, continue_url_value: str, device_id: str, **kwargs):
            self.extract_calls.append({
                "continue_url_value": continue_url_value,
                "device_id": device_id,
                "kwargs": kwargs,
            })
            if self.raise_phone_requirement:
                raise RuntimeError("Sub2 授权登录遇到 add-phone 步骤")
            return {"code": "sub2-code", "state": "sub2-state", "scope": ""}

    fake_auth_service = FakeAuthService()
    openai_auth_module.openai_auth_service = fake_auth_service
    openai_push_module.openai_auth_service = fake_auth_service
    openai_push_module.openai_push_service = openai_push_module.OpenAiPushService(
        duck_repository_module.duck_repository,
        mail_repository_module.mail_repository,
        fake_auth_service,
        openai_push_module.telegram_service,
    )
    openai_push_module.openai_push_job_service = openai_push_module.OpenAiDuckPushJobService(openai_push_module.openai_push_service)
    openai_push_api_module.openai_push_job_service = openai_push_module.openai_push_job_service
    duck_api_module.duck_service = duck_service_module.duck_service
    return (
        duck_repository_module.duck_repository,
        settings_repository_module.settings_repository,
        mail_repository_module.mail_repository,
        fake_auth_service,
        calls,
    )


def wait_openai_duck_push_job(test_client, job_id: str) -> dict:
    deadline = time.time() + 3
    body: dict = {}
    while time.time() < deadline:
        response = test_client.get(f"/api/openai/duck-push-sub2/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError(f"OpenAI Duck 推送任务未完成：{body}")


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
    repository, settings, _mail_repository, _auth_service, calls = reset_openai_push_service(tmp_path, monkeypatch, responses)
    settings.set("sub2.apiUrl", "https://sub2.example.com")
    settings.set("sub2.apiKey", "adminkey")
    settings.set("sub2.defaultGroupId", "9")
    address_id = create_duck_address_with_credentials(repository)

    response = test_client.post("/api/openai/duck-push-sub2", json={"duckAddressId": address_id})

    assert response.status_code == 200
    started = response.json()
    assert started["success"] is True
    assert started["status"] == "running"
    body = wait_openai_duck_push_job(test_client, started["jobId"])["result"]
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


def test_openai_duck_push_uses_configured_default_proxy_id(tmp_path, monkeypatch, test_client) -> None:
    responses = [
        httpx.Response(200, json={"code": 0, "data": {"id": 100}}),
    ]
    repository, settings, _mail_repository, _auth_service, calls = reset_openai_push_service(tmp_path, monkeypatch, responses)
    settings.set("sub2.apiUrl", "https://sub2.example.com")
    settings.set("sub2.apiKey", "adminkey")
    settings.set("sub2.defaultGroupId", "9")
    settings.set("sub2.defaultProxyId", "77")
    address_id = create_duck_address_with_credentials(repository)

    response = test_client.post("/api/openai/duck-push-sub2", json={"duckAddressId": address_id})

    assert response.status_code == 200
    started = response.json()
    body = wait_openai_duck_push_job(test_client, started["jobId"])["result"]
    assert body["success"] is True
    assert body["data"]["accounts"][0]["group_ids"] == [9]
    assert len(calls) == 1
    assert calls[0]["url"] == "https://sub2.example.com/api/v1/admin/accounts"
    assert calls[0]["json"]["proxy_id"] == 77


def create_mailbox(mail_repository) -> None:
    mail_repository.upsert_mailbox({
        "id": "root@claw.163.com",
        "email": "root@claw.163.com",
        "prefix": "root",
        "display_name": "Root",
        "account_id": "claw:1",
        "status": "active",
        "connection_id": "legacy",
        "provider_mailbox_id": "root",
    })


def test_openai_duck_push_auto_login_when_auth_json_missing(tmp_path, monkeypatch, test_client) -> None:
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
        httpx.Response(200, json={"code": 0, "data": {
            "auth_url": "https://auth.openai.com/oauth?state=sub2-state",
            "session_id": "session-1",
        }}),
        httpx.Response(200, json={"code": 0, "data": {"id": 100}}),
    ]
    repository, settings, mail_repository, auth_service, calls = reset_openai_push_service(tmp_path, monkeypatch, responses)
    settings.set("sub2.apiUrl", "https://sub2.example.com")
    settings.set("sub2.apiKey", "adminkey")
    settings.set("sub2.defaultGroupId", "9")
    repository.create_account("duck:1", "main", "token")
    create_mailbox(mail_repository)
    address = repository.save_address({
        "account_id": "duck:1",
        "address": "private@duck.com",
        "local_part": "private",
        "forwarding_mailbox_email": "root@claw.163.com",
        "note": None,
        "raw_json": "{}",
    })

    response = test_client.post("/api/openai/duck-push-sub2", json={"duckAddressId": address["id"]})

    assert response.status_code == 200
    started = response.json()
    assert started["success"] is True
    assert started["status"] == "running"
    body = wait_openai_duck_push_job(test_client, started["jobId"])["result"]
    assert body["success"] is True
    assert body["pushMode"] == "sub2_auth"
    assert body["email"] == "private@duck.com"
    assert auth_service.login_calls
    assert auth_service.extract_calls
    assert len(calls) == 3
    assert calls[0]["url"] == "https://sub2.example.com/api/v1/admin/proxies?page=1&page_size=1&status=active&sort_by=id&sort_order=desc"
    assert calls[1]["url"] == "https://sub2.example.com/api/v1/admin/openai/generate-auth-url"
    assert calls[1]["json"] == {"proxy_id": 5}
    assert calls[2]["url"] == "https://sub2.example.com/api/v1/admin/openai/create-from-oauth"
    assert calls[2]["json"]["session_id"] == "session-1"
    assert calls[2]["json"]["code"] == "sub2-code"
    assert calls[2]["json"]["state"] == "sub2-state"
    assert calls[2]["json"]["proxy_id"] == 5
    saved_address = repository.get_address(address["id"])
    assert saved_address is not None
    assert saved_address["openai_auth_json"] is not None
    assert saved_address["sub2_push_mode"] == "sub2_auth"
    assert saved_address["sub2_push_email"] == "private@duck.com"


def test_openai_duck_push_falls_back_to_oauth_token_on_add_phone(tmp_path, monkeypatch, test_client) -> None:
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
        httpx.Response(200, json={"code": 0, "data": {
            "auth_url": "https://auth.openai.com/add-phone?state=sub2-state",
            "session_id": "session-1",
        }}),
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
    repository, settings, mail_repository, auth_service, calls = reset_openai_push_service(tmp_path, monkeypatch, responses)
    settings.set("sub2.apiUrl", "https://sub2.example.com")
    settings.set("sub2.apiKey", "adminkey")
    settings.set("sub2.defaultGroupId", "9")
    repository.create_account("duck:1", "main", "token")
    create_mailbox(mail_repository)
    auth_service.raise_phone_requirement = True
    address = repository.save_address({
        "account_id": "duck:1",
        "address": "private@duck.com",
        "local_part": "private",
        "forwarding_mailbox_email": "root@claw.163.com",
        "note": None,
        "raw_json": "{}",
    })

    response = test_client.post("/api/openai/duck-push-sub2", json={"duckAddressId": address["id"]})

    assert response.status_code == 200
    started = response.json()
    assert started["success"] is True
    assert started["status"] == "running"
    body = wait_openai_duck_push_job(test_client, started["jobId"])["result"]
    assert body["success"] is True
    assert body["pushMode"] == "fallback_oauth_token"
    assert "add-phone" in body["fallbackReason"]
    assert body["data"]["accounts"][0]["group_ids"] == [9]
    assert len(calls) == 4
    assert calls[0]["url"] == "https://sub2.example.com/api/v1/admin/proxies?page=1&page_size=1&status=active&sort_by=id&sort_order=desc"
    assert calls[1]["url"] == "https://sub2.example.com/api/v1/admin/openai/generate-auth-url"
    assert calls[2]["url"] == "https://sub2.example.com/api/v1/admin/proxies?page=1&page_size=1&status=active&sort_by=id&sort_order=desc"
    assert calls[3]["url"] == "https://sub2.example.com/api/v1/admin/accounts"


def test_openai_duck_push_skips_sub2_auth_login_when_disabled(tmp_path, monkeypatch, test_client) -> None:
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
    repository, settings, mail_repository, auth_service, calls = reset_openai_push_service(tmp_path, monkeypatch, responses)
    settings.set("sub2.apiUrl", "https://sub2.example.com")
    settings.set("sub2.apiKey", "adminkey")
    settings.set("sub2.defaultGroupId", "9")
    settings.set("sub2.openAiAuthLoginEnabled", "false")
    repository.create_account("duck:1", "main", "token")
    create_mailbox(mail_repository)
    address = repository.save_address({
        "account_id": "duck:1",
        "address": "private@duck.com",
        "local_part": "private",
        "forwarding_mailbox_email": "root@claw.163.com",
        "note": None,
        "raw_json": "{}",
    })

    response = test_client.post("/api/openai/duck-push-sub2", json={"duckAddressId": address["id"]})

    assert response.status_code == 200
    started = response.json()
    assert started["success"] is True
    body = wait_openai_duck_push_job(test_client, started["jobId"])["result"]
    assert body["success"] is True
    assert body["pushMode"] == "oauth_token"
    assert auth_service.login_calls
    assert auth_service.extract_calls == []
    assert len(calls) == 2
    assert calls[0]["url"] == "https://sub2.example.com/api/v1/admin/proxies?page=1&page_size=1&status=active&sort_by=id&sort_order=desc"
    assert calls[1]["url"] == "https://sub2.example.com/api/v1/admin/accounts"
    saved_address = repository.get_address(address["id"])
    assert saved_address is not None
    assert saved_address["sub2_push_mode"] == "oauth_token"
