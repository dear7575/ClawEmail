from types import SimpleNamespace


def test_openai_duck_login_saves_oauth_for_existing_account(monkeypatch, test_client) -> None:
    import app.api.duck as duck_api

    address = {
        "id": 7,
        "address": "user@duck.com",
        "status": "active",
        "forwarding_mailbox_email": "inbox@claw.example",
    }
    mailbox = {"email": "inbox@claw.example", "connection_id": "connection-1", "status": "active"}
    login_calls: list[tuple[dict, str, dict]] = []
    monkeypatch.setattr(duck_api.duck_service.repository, "get_address", lambda _address_id: address)
    monkeypatch.setattr(duck_api.mail_repository, "get_mailbox_by_email", lambda _email: mailbox)
    monkeypatch.setattr(duck_api.openai_auth_service.mail_client, "api_key", lambda _connection_id: "test-key")
    monkeypatch.setattr(
        duck_api.openai_auth_service,
        "login_with_email_otp",
        lambda duck_address, target_email, inbox_mailbox: (
            login_calls.append((duck_address, target_email, inbox_mailbox))
            or SimpleNamespace(token={"email": target_email})
        ),
    )

    response = test_client.post("/api/duck/addresses/7/openai-login")

    assert response.status_code == 200
    assert response.json() == {"success": True, "email": "user@duck.com", "hasAuthJson": True}
    assert login_calls == [(address, "user@duck.com", mailbox)]


def test_openai_duck_login_requires_forwarding_mailbox(monkeypatch, test_client) -> None:
    import app.api.duck as duck_api

    monkeypatch.setattr(
        duck_api.duck_service.repository,
        "get_address",
        lambda _address_id: {"id": 7, "status": "active", "address": "user@duck.com"},
    )

    response = test_client.post("/api/duck/addresses/7/openai-login")

    assert response.status_code == 400
