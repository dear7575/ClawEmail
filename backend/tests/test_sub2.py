import importlib
from collections import deque

import httpx
from app.main import app


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
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "json": json,
            "kwargs": self.kwargs,
        })
        return self.responses.popleft()


def reset_sub2_service(tmp_path, monkeypatch, responses: list[httpx.Response] | None = None):
    database_url = f"sqlite:///{tmp_path / 'settings.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("SUB2_API_URL", raising=False)
    monkeypatch.delenv("SUB2_API_KEY", raising=False)
    import app.core.config as config_module
    import app.db.settings_repository as repository_module
    import app.services.network_settings as network_service_module
    import app.services.sub2 as sub2_service_module

    config_module.get_settings.cache_clear()
    importlib.reload(repository_module)
    importlib.reload(network_service_module)
    importlib.reload(sub2_service_module)
    import app.api.system as system_module
    import app.api.sub2 as sub2_api_module

    system_module.network_settings_service = network_service_module.network_settings_service
    calls: list[dict] = []
    if responses is not None:
        response_queue = deque(responses)
        sub2_service_module.sub2_service = sub2_service_module.Sub2Service(
            repository=repository_module.settings_repository,
            client_factory=lambda **kwargs: RecordingClient(response_queue, calls, **kwargs),
        )
        sub2_api_module.sub2_service = sub2_service_module.sub2_service
    else:
        sub2_api_module.sub2_service = sub2_service_module.sub2_service
    return repository_module.settings_repository, calls


def test_get_sub2_settings_returns_defaults(tmp_path, monkeypatch, test_client) -> None:
    reset_sub2_service(tmp_path, monkeypatch)
    client = test_client

    response = client.get("/api/sub2/settings")

    assert response.status_code == 200
    assert response.json() == {
        "apiUrl": "",
        "hasApiKey": False,
        "apiKeyPreview": None,
        "defaultGroupId": None,
        "openAiAuthLoginEnabled": True,
    }


def test_put_sub2_settings_persists_compatible_keys(tmp_path, monkeypatch, test_client) -> None:
    repository, _calls = reset_sub2_service(tmp_path, monkeypatch)
    client = test_client

    response = client.put("/api/sub2/settings", json={
        "apiUrl": " https://sub2.example.com ",
        "apiKey": " 1234567890abcdef ",
        "defaultGroupId": 12,
        "openAiAuthLoginEnabled": False,
    })

    assert response.status_code == 200
    assert response.json() == {
        "apiUrl": "https://sub2.example.com",
        "hasApiKey": True,
        "apiKeyPreview": "12345678...cdef",
        "defaultGroupId": 12,
        "openAiAuthLoginEnabled": False,
    }
    assert repository.get("sub2.apiUrl") == "https://sub2.example.com"
    assert repository.get("sub2.apiKey") == "1234567890abcdef"
    assert repository.get("sub2.defaultGroupId") == "12"
    assert repository.get("sub2.openAiAuthLoginEnabled") == "false"


def test_put_sub2_settings_preserves_api_key_when_omitted(tmp_path, monkeypatch, test_client) -> None:
    repository, _calls = reset_sub2_service(tmp_path, monkeypatch)
    repository.set("sub2.apiKey", "secret-token")
    repository.set("sub2.defaultGroupId", "9")
    client = test_client

    response = client.put("/api/sub2/settings", json={
        "apiUrl": "https://sub2.example.com",
    })

    assert response.status_code == 200
    assert response.json()["hasApiKey"] is True
    assert response.json()["apiKeyPreview"] == "secr****"
    assert response.json()["defaultGroupId"] == 9
    assert response.json()["openAiAuthLoginEnabled"] is True
    assert repository.get("sub2.apiKey") == "secret-token"
    assert repository.get("sub2.defaultGroupId") == "9"


def test_put_sub2_settings_clears_group_id_when_null(tmp_path, monkeypatch, test_client) -> None:
    repository, _calls = reset_sub2_service(tmp_path, monkeypatch)
    repository.set("sub2.defaultGroupId", "9")
    client = test_client

    response = client.put("/api/sub2/settings", json={"defaultGroupId": None})

    assert response.status_code == 200
    assert response.json()["defaultGroupId"] is None
    assert repository.get("sub2.defaultGroupId") == ""


def test_put_sub2_settings_rejects_invalid_group_id(tmp_path, monkeypatch, test_client) -> None:
    reset_sub2_service(tmp_path, monkeypatch)
    client = test_client

    response = client.put("/api/sub2/settings", json={"defaultGroupId": 0})

    assert response.status_code == 422


def test_get_sub2_groups_fetches_openai_active_groups(tmp_path, monkeypatch, test_client) -> None:
    response = httpx.Response(200, json={"data": {"items": [
        {"id": "7", "name": "OpenAI"},
        {"id": 8},
        {"id": "bad", "name": "ignored"},
    ]}})
    repository, calls = reset_sub2_service(tmp_path, monkeypatch, [response])
    repository.set("sub2.apiUrl", "https://sub2.example.com")
    repository.set("sub2.apiKey", "adminkey")
    client = test_client

    result = client.get("/api/sub2/groups")

    assert result.status_code == 200
    assert result.json() == {"items": [{"id": 7, "name": "OpenAI"}, {"id": 8, "name": None}]}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "https://sub2.example.com/api/v1/admin/groups?page=1&page_size=1000&platform=openai&status=active"
    assert calls[0]["headers"] == {"x-api-key": "adminkey"}


def test_get_sub2_groups_uses_bearer_authorization(tmp_path, monkeypatch, test_client) -> None:
    response = httpx.Response(200, json=[])
    repository, calls = reset_sub2_service(tmp_path, monkeypatch, [response])
    repository.set("sub2.apiUrl", "https://sub2.example.com/api/v1/admin/accounts/data")
    repository.set("sub2.apiKey", "Bearer token")
    client = test_client

    result = client.get("/api/sub2/groups")

    assert result.status_code == 200
    assert calls[0]["url"] == "https://sub2.example.com/api/v1/admin/groups?page=1&page_size=1000&platform=openai&status=active"
    assert calls[0]["headers"] == {"authorization": "Bearer token"}


def chatgpt_session() -> dict:
    return {
        "user": {
            "email": "user@example.com",
            "id": "user-id",
        },
        "account": {
            "id": "account-id",
            "planType": "plus",
        },
        "accessToken": "access-token",
        "expires": "2026-05-13T12:00:00Z",
    }


def test_convert_sub2_account_matches_legacy_shape(tmp_path, monkeypatch, test_client) -> None:
    reset_sub2_service(tmp_path, monkeypatch)
    client = test_client

    response = client.post("/api/sub2/convert", json={"input": chatgpt_session()})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["proxies"] == []
    assert data["accounts"][0]["name"] == "user@example.com"
    assert data["accounts"][0]["platform"] == "openai"
    assert data["accounts"][0]["type"] == "oauth"
    assert data["accounts"][0]["credentials"]["access_token"] == "access-token"
    assert data["accounts"][0]["credentials"]["expires_at"] == "2026-05-13T20:00:00+08:00"
    assert data["accounts"][0]["proxy_key"] == ""
    assert data["accounts"][0]["auto_pause_on_expired"] is True


def test_push_sub2_account_uses_default_proxy_and_creates_account(tmp_path, monkeypatch, test_client) -> None:
    responses = [
        httpx.Response(200, json={"data": {"items": [{
            "id": 5,
            "name": "proxy-5",
            "protocol": "http",
            "host": "127.0.0.1",
            "port": 7890,
            "username": "u",
            "password": "",
        }]}}),
        httpx.Response(200, json={"code": 0, "data": {"id": 100}}),
    ]
    repository, calls = reset_sub2_service(tmp_path, monkeypatch, responses)
    repository.set("sub2.apiUrl", "https://sub2.example.com")
    repository.set("sub2.apiKey", "adminkey")
    repository.set("sub2.defaultGroupId", "12")
    client = test_client

    response = client.post("/api/sub2/push", json={"input": chatgpt_session()})

    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["data"]["proxies"][0]["id"] == 5
    assert result["data"]["accounts"][0]["proxy_key"] == "http|127.0.0.1|7890|u|"
    assert result["data"]["accounts"][0]["group_ids"] == [12]
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "https://sub2.example.com/api/v1/admin/proxies?page=1&page_size=1&status=active&sort_by=id&sort_order=desc"
    assert calls[1]["method"] == "POST"
    assert calls[1]["url"] == "https://sub2.example.com/api/v1/admin/accounts"
    assert calls[1]["json"]["proxy_id"] == 5
    assert calls[1]["json"]["group_ids"] == [12]
    assert calls[1]["json"]["confirm_mixed_channel_risk"] is True
    assert "notes" not in calls[1]["json"]


def test_push_sub2_account_reuses_matching_proxy_without_create(tmp_path, monkeypatch, test_client) -> None:
    monkeypatch.setenv("SUB2_PROXY_TEMPLATE_JSON", '{"proxies":[{"proxy_key":"p1","name":"local","protocol":"http","host":"1.2.3.4","port":8080,"username":"u","password":"secret"}]}')
    responses = [
        httpx.Response(200, json={"data": {"items": [{
            "id": 9,
            "protocol": "http",
            "host": "1.2.3.4",
            "port": 8080,
            "username": "u",
            "password": "******",
        }]}}),
        httpx.Response(200, json={"code": 0, "data": {"id": 101}}),
    ]
    repository, calls = reset_sub2_service(tmp_path, monkeypatch, responses)
    repository.set("sub2.apiUrl", "https://sub2.example.com")
    repository.set("sub2.apiKey", "adminkey")
    client = test_client

    response = client.post("/api/sub2/push", json={"input": chatgpt_session(), "groupId": 13})

    assert response.status_code == 200
    assert len(calls) == 2
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "https://sub2.example.com/api/v1/admin/proxies?page=1&page_size=20&protocol=http&status=active&search=1.2.3.4&sort_by=id&sort_order=desc"
    assert calls[1]["method"] == "POST"
    assert calls[1]["url"] == "https://sub2.example.com/api/v1/admin/accounts"
    assert calls[1]["json"]["proxy_id"] == 9


def test_push_sub2_account_via_auth_login_uses_openai_oauth_endpoints(tmp_path, monkeypatch) -> None:
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
            "auth_url": "https://auth.openai.com/oauth?state=generated-state",
            "session_id": "session-1",
        }}),
        httpx.Response(200, json={"code": 0, "data": {"id": 100}}),
    ]
    repository, calls = reset_sub2_service(tmp_path, monkeypatch, responses)
    repository.set("sub2.apiUrl", "https://sub2.example.com")
    repository.set("sub2.apiKey", "adminkey")
    repository.set("sub2.defaultGroupId", "12")
    import app.services.sub2 as sub2_service_module

    authorized_requests = []

    def authorize(request):
        authorized_requests.append(request)
        return sub2_service_module.Sub2AuthLoginCallback(code="oauth-code", state="", scope="")

    result = sub2_service_module.sub2_service.push_data_via_auth_login(
        sub2_service_module.convert_openai_oauth_to_sub2({
            "email": "user@example.com",
            "accessToken": "access-token",
            "expiresAt": "2026-05-13T12:00:00Z",
            "userId": "user-id",
            "accountId": "account-id",
            "planType": "free",
        }),
        None,
        authorize,
    )

    assert result["data"]["accounts"][0]["group_ids"] == [12]
    assert len(authorized_requests) == 1
    assert authorized_requests[0].auth_url == "https://auth.openai.com/oauth?state=generated-state"
    assert authorized_requests[0].session_id == "session-1"
    assert authorized_requests[0].proxy_id == 5
    assert calls[0]["url"] == "https://sub2.example.com/api/v1/admin/proxies?page=1&page_size=1&status=active&sort_by=id&sort_order=desc"
    assert calls[1]["url"] == "https://sub2.example.com/api/v1/admin/openai/generate-auth-url"
    assert calls[1]["json"] == {"proxy_id": 5}
    assert calls[2]["url"] == "https://sub2.example.com/api/v1/admin/openai/create-from-oauth"
    assert calls[2]["json"]["session_id"] == "session-1"
    assert calls[2]["json"]["code"] == "oauth-code"
    assert calls[2]["json"]["state"] == "generated-state"
    assert calls[2]["json"]["proxy_id"] == 5
