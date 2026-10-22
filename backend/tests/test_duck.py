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

    def request(self, method: str, url: str, headers: dict[str, str], **kwargs):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "request_kwargs": kwargs,
            "kwargs": self.kwargs,
        })
        return self.responses.popleft()


def reset_duck_service(tmp_path, monkeypatch, responses: list[httpx.Response] | None = None):
    database_url = f"sqlite:///{tmp_path / 'duck.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    import app.core.config as config_module
    import app.db.duck_repository as duck_repository_module
    import app.db.settings_repository as settings_repository_module
    import app.services.network_settings as network_service_module
    import app.services.duck as duck_service_module

    config_module.get_settings.cache_clear()
    importlib.reload(settings_repository_module)
    importlib.reload(duck_repository_module)
    importlib.reload(network_service_module)
    importlib.reload(duck_service_module)
    import app.api.system as system_module
    import app.api.duck as duck_api_module

    system_module.network_settings_service = network_service_module.network_settings_service
    calls: list[dict] = []
    if responses is not None:
        duck_service_module.duck_service = duck_service_module.DuckService(
            repository=duck_repository_module.duck_repository,
            client_factory=lambda **kwargs: RecordingClient(responses, calls, **kwargs),
        )
        duck_api_module.duck_service = duck_service_module.duck_service
    else:
        duck_api_module.duck_service = duck_service_module.duck_service
    return duck_repository_module.duck_repository, calls


def create_account(client) -> dict:
    response = client.post("/api/duck/accounts", json={
        "label": "main",
        "token": "Bearer abcdefghijklmnop",
    })
    assert response.status_code == 201
    return response.json()


def test_create_and_list_duck_accounts_masks_token(tmp_path, monkeypatch, test_client) -> None:
    reset_duck_service(tmp_path, monkeypatch)
    client = test_client

    account = create_account(client)
    listed = client.get("/api/duck/accounts")

    assert account["label"] == "main"
    assert account["token_prefix"] == "abcdefgh"
    assert account["token_suffix"] == "mnop"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == account["id"]
    assert "token" not in listed.json()["items"][0]


def test_update_and_delete_duck_account(tmp_path, monkeypatch, test_client) -> None:
    reset_duck_service(tmp_path, monkeypatch)
    client = test_client
    account = create_account(client)

    updated = client.patch(f"/api/duck/accounts/{account['id']}", json={"token": "Bearer zzzzzzzzzzzzzz"})
    deleted = client.delete(f"/api/duck/accounts/{account['id']}")
    listed = client.get("/api/duck/accounts")

    assert updated.status_code == 200
    assert updated.json()["token_prefix"] == "zzzzzzzz"
    assert deleted.status_code == 200
    assert deleted.json() == {"success": True}
    assert listed.json()["items"] == []


def test_generate_duck_address_persists_public_shape(tmp_path, monkeypatch, test_client) -> None:
    responses = [httpx.Response(200, json={"address": "Private-Address"})]
    _repository, calls = reset_duck_service(tmp_path, monkeypatch, responses)
    client = test_client
    account = create_account(client)

    response = client.post(f"/api/duck/accounts/{account['id']}/addresses", json={
        "forwardingMailboxEmail": "TARGET@EXAMPLE.COM",
        "note": " first ",
    })

    assert response.status_code == 201
    address = response.json()
    assert address["account_id"] == account["id"]
    assert address["address"] == "private-address@duck.com"
    assert address["local_part"] == "private-address"
    assert address["forwarding_mailbox_email"] == "target@example.com"
    assert address["note"] == "first"
    assert address["has_openai_password"] is False
    assert "is_sub2_pushed" not in address
    assert "sub2_pushed_at" not in address
    assert "sub2_push_mode" not in address
    assert "sub2_push_email" not in address
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://quack.duckduckgo.com/api/email/addresses"
    assert calls[0]["headers"]["authorization"] == "Bearer abcdefghijklmnop"
    assert calls[0]["headers"]["accept"] == "*/*"
    assert calls[0]["headers"]["origin"] == "https://duckduckgo.com"
    assert calls[0]["headers"]["referer"] == "https://duckduckgo.com/"
    assert calls[0]["headers"]["sec-fetch-site"] == "same-site"
    assert "Mozilla/5.0" in calls[0]["headers"]["user-agent"]
    assert calls[0]["request_kwargs"]["content"] == b""


def test_list_duck_addresses_supports_pagination_and_keyword(tmp_path, monkeypatch, test_client) -> None:
    reset_duck_service(tmp_path, monkeypatch)
    client = test_client
    account = create_account(client)
    import app.api.duck as duck_api_module

    duck_api_module.duck_service.repository.save_address({
        "account_id": account["id"],
        "address": "first@duck.com",
        "local_part": "first",
        "forwarding_mailbox_email": "alpha@claw.163.com",
        "note": "alpha note",
        "raw_json": "{}",
    })
    duck_api_module.duck_service.repository.save_address({
        "account_id": account["id"],
        "address": "second@duck.com",
        "local_part": "second",
        "forwarding_mailbox_email": "beta@claw.163.com",
        "note": "beta note",
        "raw_json": "{}",
    })

    paged = client.get(f"/api/duck/addresses?accountId={account['id']}&limit=1&offset=1")
    searched = client.get(f"/api/duck/addresses?accountId={account['id']}&keyword=alpha")

    assert paged.status_code == 200
    assert paged.json()["total"] == 2
    assert paged.json()["count"] == 2
    assert paged.json()["limit"] == 1
    assert paged.json()["offset"] == 1
    assert len(paged.json()["items"]) == 1
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["address"] == "first@duck.com"


def test_update_duck_address_and_openai_password(tmp_path, monkeypatch, test_client) -> None:
    responses = [httpx.Response(200, json={"address": "private"})]
    reset_duck_service(tmp_path, monkeypatch, responses)
    client = test_client
    account = create_account(client)
    created = client.post(f"/api/duck/accounts/{account['id']}/addresses", json={}).json()

    updated = client.patch(f"/api/duck/addresses/{created['id']}", json={
        "forwardingMailboxEmail": "",
        "note": "updated",
    })
    credentials = client.patch(f"/api/duck/addresses/{created['id']}/openai-password", json={
        "password": "pw",
    })
    password = client.get(f"/api/duck/addresses/{created['id']}/openai-password")
    login = client.post(f"/api/duck/addresses/{created['id']}/openai-login")
    auth_json = client.get(f"/api/duck/addresses/{created['id']}/openai-auth-json")

    assert updated.status_code == 200
    assert updated.json()["forwarding_mailbox_email"] is None
    assert updated.json()["note"] == "updated"
    assert credentials.status_code == 200
    assert credentials.json()["has_openai_password"] is True
    assert "has_openai_auth_json" not in credentials.json()
    assert password.json() == {"password": "pw"}
    assert login.status_code == 404
    assert auth_json.status_code == 404


def test_delete_duck_address(tmp_path, monkeypatch, test_client) -> None:
    responses = [httpx.Response(200, json={"address": "private"})]
    reset_duck_service(tmp_path, monkeypatch, responses)
    client = test_client
    account = create_account(client)
    created = client.post(f"/api/duck/accounts/{account['id']}/addresses", json={}).json()

    response = client.delete(f"/api/duck/addresses/{created['id']}")
    listed = client.get("/api/duck/addresses")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert listed.json()["items"] == []


def test_repeated_duck_address_deletes_keep_listener_endpoint_available(tmp_path, monkeypatch, test_client) -> None:
    reset_duck_service(tmp_path, monkeypatch)
    client = test_client
    account = create_account(client)
    import app.api.duck as duck_api_module

    address_ids: list[int] = []
    for index in range(20):
        row = duck_api_module.duck_service.repository.save_address({
            "account_id": account["id"],
            "address": f"delete-{index}@duck.com",
            "local_part": f"delete-{index}",
            "forwarding_mailbox_email": "target@claw.163.com",
            "note": "delete stress",
            "raw_json": "{}",
        })
        address_ids.append(row["id"])

    for address_id in address_ids:
        deleted = client.delete(f"/api/duck/addresses/{address_id}")
        listeners = client.get("/api/listeners")

        assert deleted.status_code == 200
        assert deleted.json() == {"success": True}
        assert listeners.status_code == 200
        assert "items" in listeners.json()

    listed = client.get("/api/duck/addresses")
    assert listed.status_code == 200
    assert listed.json()["total"] == 0


def test_fresh_duck_schema_omits_oauth_column(tmp_path, monkeypatch) -> None:
    from sqlalchemy import text

    repository, _calls = reset_duck_service(tmp_path, monkeypatch)
    with repository.engine.connect() as connection:
        columns = connection.execute(text("PRAGMA table_info(duck_addresses)")).mappings().all()

    assert "openai_auth_json" not in {column["name"] for column in columns}


def test_legacy_oauth_column_is_removed_without_losing_duck_data(tmp_path) -> None:
    from sqlalchemy import create_engine, text

    from app.db.duck_repository import DuckRepository

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-duck.db'}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE duck_addresses (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              account_id TEXT NOT NULL,
              address TEXT NOT NULL UNIQUE,
              local_part TEXT NOT NULL,
              forwarding_mailbox_email TEXT,
              note TEXT,
              openai_password TEXT,
              openai_auth_json TEXT,
              sub2_pushed_at TEXT,
              sub2_push_mode TEXT,
              sub2_push_email TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              raw_json TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO duck_addresses (
              account_id, address, local_part, forwarding_mailbox_email, note,
              openai_password, openai_auth_json, sub2_pushed_at, sub2_push_mode,
              sub2_push_email, raw_json
            ) VALUES (
              'legacy-account', 'legacy@duck.com', 'legacy', 'target@example.com', 'keep-note',
              'keep-password', '{\"access_token\":\"legacy-token\"}', '2026-01-01', 'api',
              'push@example.com', '{\"source\":\"legacy\"}'
            )
        """))

    repository = DuckRepository(engine)
    address = repository.get_address(1)
    with engine.connect() as connection:
        columns = connection.execute(text("PRAGMA table_info(duck_addresses)")).mappings().all()

    assert address is not None
    assert address["address"] == "legacy@duck.com"
    assert address["forwarding_mailbox_email"] == "target@example.com"
    assert address["note"] == "keep-note"
    assert address["openai_password"] == "keep-password"
    assert address["sub2_pushed_at"] == "2026-01-01"
    assert address["sub2_push_mode"] == "api"
    assert address["sub2_push_email"] == "push@example.com"
    assert address["raw_json"] == '{"source":"legacy"}'
    assert "openai_auth_json" not in {column["name"] for column in columns}

    DuckRepository(engine)
    engine.dispose()
