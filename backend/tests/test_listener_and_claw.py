import importlib

from app.main import app


class FakeDashboard:
    def __init__(self) -> None:
        self.sent_codes: list[str] = []
        self.verified_codes: list[tuple[str, str, str | None]] = []
        self.auth_failures = 0

    def send_login_code(self, email: str) -> str:
        self.sent_codes.append(email)
        return "pending-cookie"

    def verify_login_code(self, email: str, code: str, pending_cookie: str | None = None) -> str:
        self.verified_codes.append((email, code, pending_cookie))
        return "dashboard-cookie"

    def get_auth_me(self, cookie: str) -> dict:
        if self.auth_failures > 0:
            self.auth_failures -= 1
            raise RuntimeError("Claw dashboard error: unauthorized")
        return {"email": "user@163.com"}

    def list_workspaces(self, cookie: str) -> list[dict]:
        return [{"id": "workspace-1", "name": "Workspace", "status": "active"}]

    def list_api_keys(self, cookie: str) -> list[dict]:
        return [{"apiKey": "api-key-1234567890", "status": "active", "defaultFlag": 1}]

    def list_mailboxes(
        self,
        cookie: str | None = None,
        workspace_id: str | None = None,
        connection_id: str | None = None,
    ) -> list[dict]:
        return [{
            "id": "mailbox-root",
            "email": "root@claw.163.com",
            "prefix": "root",
            "mailbox_type": "primary",
            "status": "active",
        }]


def reset_local_services(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'local.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    for key in (
        "CLAW_API_KEY",
        "CLAW_DASHBOARD_COOKIE",
        "CLAW_WORKSPACE_ID",
        "CLAW_PARENT_MAILBOX_ID",
        "CLAW_ROOT_PREFIX",
        "CLAW_DOMAIN",
    ):
        monkeypatch.setenv(key, "")
    import app.core.config as config_module
    import app.db.settings_repository as settings_repository_module
    import app.db.claw_repository as claw_repository_module
    import app.db.mail_repository as mail_repository_module
    import app.services.listener_settings as listener_settings_module
    import app.services.listeners as listeners_module
    import app.services.inbox_sync_scheduler as inbox_sync_scheduler_module
    import app.services.claw_auth as claw_auth_module
    import app.services.mails as mails_service_module

    config_module.get_settings.cache_clear()
    importlib.reload(settings_repository_module)
    importlib.reload(claw_repository_module)
    importlib.reload(mail_repository_module)
    importlib.reload(listener_settings_module)
    importlib.reload(listeners_module)
    importlib.reload(inbox_sync_scheduler_module)
    importlib.reload(claw_auth_module)
    importlib.reload(mails_service_module)
    import app.api.events as events_api_module
    import app.api.claw_auth as claw_auth_api_module
    import app.api.mailboxes as mailboxes_api_module
    import app.api.mails as mails_api_module

    events_api_module.listener_settings_service = listener_settings_module.listener_settings_service
    events_api_module.listener_manager = listeners_module.listener_manager
    events_api_module.listener_manager.worker_enabled = False
    inbox_sync_scheduler_module.inbox_sync_scheduler.settings_service = listener_settings_module.listener_settings_service
    claw_auth_api_module.claw_auth_service = claw_auth_module.claw_auth_service
    mailboxes_api_module.mailbox_service.repository = mail_repository_module.mail_repository
    mails_api_module.mail_service = mails_service_module.mail_service
    return settings_repository_module.settings_repository, claw_repository_module.claw_repository


def test_listener_settings_defaults_and_save(tmp_path, monkeypatch, test_client) -> None:
    repository, _claw_repository = reset_local_services(tmp_path, monkeypatch)
    client = test_client

    defaults = client.get("/api/listener-settings")
    saved = client.put("/api/listener-settings", json={
        "logMode": "verbose",
        "reconnectMode": "slow",
        "inboxSyncInterval": "60",
    })

    assert defaults.status_code == 200
    assert defaults.json() == {"logMode": "quiet", "reconnectMode": "standard", "inboxSyncInterval": "manual"}
    assert saved.status_code == 200
    assert saved.json() == {"logMode": "verbose", "reconnectMode": "slow", "inboxSyncInterval": "60"}
    assert repository.get("listener.logMode") == "verbose"
    assert repository.get("listener.reconnectMode") == "slow"
    assert repository.get("inbox.syncInterval") == "60"


def test_listeners_returns_empty_snapshot_during_migration(tmp_path, monkeypatch, test_client) -> None:
    reset_local_services(tmp_path, monkeypatch)
    client = test_client

    response = client.get("/api/listeners")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_claw_status_returns_disconnected_when_unconfigured(tmp_path, monkeypatch, test_client) -> None:
    reset_local_services(tmp_path, monkeypatch)
    client = test_client

    response = client.get("/api/auth/claw/status")

    assert response.status_code == 200
    assert response.json() == {
        "id": None,
        "connected": False,
        "hasApiKey": False,
        "hasDashboardCookie": False,
        "userEmail": None,
        "workspaceId": None,
        "workspaceName": None,
        "parentMailboxId": None,
        "rootPrefix": None,
        "domain": None,
        "apiKeyPrefix": None,
        "apiKeySuffix": None,
        "status": None,
        "label": None,
    }


def test_claw_status_uses_legacy_settings_fallback(tmp_path, monkeypatch, test_client) -> None:
    repository, claw_repository = reset_local_services(tmp_path, monkeypatch)
    import app.api.claw_auth as claw_auth_api_module

    claw_auth_api_module.claw_auth_service.dashboard = FakeDashboard()
    repository.set("claw.apiKey", "api-key-1234567890")
    repository.set("claw.dashboardCookie", "cookie")
    repository.set("claw.userEmail", "user@163.com")
    repository.set("claw.workspaceId", "workspace")
    repository.set("claw.workspaceName", "Workspace")
    repository.set("claw.parentMailboxId", "mailbox")
    repository.set("claw.rootPrefix", "root")
    repository.set("claw.domain", "claw.163.com")
    client = test_client

    response = client.get("/api/auth/claw/status")
    connections = client.get("/api/connections")

    assert response.status_code == 200
    assert response.json()["id"] == "legacy"
    assert response.json()["connected"] is True
    assert response.json()["hasApiKey"] is True
    assert response.json()["apiKeyPrefix"] == "api-key-12"
    assert response.json()["apiKeySuffix"] == "7890"
    assert claw_repository.get_connection("legacy") is not None
    assert connections.status_code == 200
    assert connections.json()["items"][0]["id"] == "legacy"


def test_list_connections_marks_invalid_session_disconnected(tmp_path, monkeypatch, test_client) -> None:
    _repository, claw_repository = reset_local_services(tmp_path, monkeypatch)
    import app.api.claw_auth as claw_auth_api_module

    dashboard = FakeDashboard()
    claw_auth_api_module.claw_auth_service.dashboard = dashboard
    claw_repository.upsert_connection({
        "id": "conn-1",
        "label": "用户",
        "user_email": "user@163.com",
        "workspace_id": "workspace-1",
        "workspace_name": "Workspace",
        "parent_mailbox_id": "mailbox-root",
        "root_prefix": "root",
        "domain": "claw.163.com",
        "api_key": "api-key-1234567890",
        "dashboard_cookie": "expired-cookie",
        "status": "active",
    })
    dashboard.auth_failures = 1
    client = test_client

    response = client.get("/api/connections")

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "conn-1"
    assert response.json()["items"][0]["status"] == "disconnected"
    assert response.json()["items"][0]["connected"] is False
    assert claw_repository.get_connection("conn-1")["status"] == "disconnected"


def test_list_connections_deduplicates_same_identity(tmp_path, monkeypatch, test_client) -> None:
    _repository, claw_repository = reset_local_services(tmp_path, monkeypatch)
    import app.api.claw_auth as claw_auth_api_module

    claw_auth_api_module.claw_auth_service.dashboard = FakeDashboard()
    for connection_id, status in (("legacy", "active"), ("user-163.com:workspace-1", "active")):
        claw_repository.upsert_connection({
            "id": connection_id,
            "label": "user@163.com",
            "user_email": "user@163.com",
            "workspace_id": "workspace-1",
            "workspace_name": "Workspace",
            "parent_mailbox_id": "mailbox-root",
            "root_prefix": "root",
            "domain": "claw.163.com",
            "api_key": "api-key-1234567890",
            "dashboard_cookie": "cookie",
            "status": status,
        })
    client = test_client

    response = client.get("/api/connections")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["user-163.com:workspace-1"]


def test_get_connection_returns_404_for_missing_connection(tmp_path, monkeypatch, test_client) -> None:
    reset_local_services(tmp_path, monkeypatch)
    client = test_client

    response = client.get("/api/connections/missing")

    assert response.status_code == 404
    assert response.json() == {"error": "connection not found"}


def test_claw_legacy_login_persists_connection_settings_and_mailboxes(tmp_path, monkeypatch, test_client) -> None:
    repository, claw_repository = reset_local_services(tmp_path, monkeypatch)
    import app.api.claw_auth as claw_auth_api_module

    dashboard = FakeDashboard()
    claw_auth_api_module.claw_auth_service.dashboard = dashboard
    client = test_client

    sent = client.post("/api/auth/claw/send-code", json={"email": "User"})
    verified = client.post("/api/auth/claw/verify-code", json={"email": "user@163.com", "code": "123456"})
    status = client.get("/api/auth/claw/status")
    mailboxes = client.get("/api/mailboxes")

    assert sent.status_code == 200
    assert sent.json() == {"success": True}
    assert dashboard.sent_codes == ["user@163.com"]
    assert dashboard.verified_codes == [("user@163.com", "123456", "pending-cookie")]
    assert verified.status_code == 200
    assert verified.json()["connection"]["id"] == "legacy"
    assert verified.json()["connection"]["connected"] is True
    assert verified.json()["syncedMailboxes"] == 1
    assert status.json()["workspaceId"] == "workspace-1"
    assert repository.get("claw.apiKey") == "api-key-1234567890"
    assert repository.get("claw.dashboardCookie") == "dashboard-cookie"
    assert claw_repository.get_connection("legacy")["parent_mailbox_id"] == "mailbox-root"
    assert mailboxes.json()["items"][0]["email"] == "root@claw.163.com"


def test_connection_login_uses_generated_connection_id_and_logout_marks_disconnected(tmp_path, monkeypatch, test_client) -> None:
    reset_local_services(tmp_path, monkeypatch)
    import app.api.claw_auth as claw_auth_api_module

    claw_auth_api_module.claw_auth_service.dashboard = FakeDashboard()
    client = test_client

    verified = client.post("/api/connections/verify-code", json={"email": "user@163.com", "code": "123456"})
    connection_id = verified.json()["connection"]["id"]
    logged_out = client.post(f"/api/connections/{connection_id}/logout")
    listed = client.get("/api/connections")

    assert verified.status_code == 200
    assert connection_id == "user-163.com:workspace-1"
    assert logged_out.status_code == 200
    assert logged_out.json()["status"] == "disconnected"
    assert logged_out.json()["connected"] is False
    assert listed.json()["items"][0]["id"] == connection_id
    assert listed.json()["items"][0]["status"] == "disconnected"


def test_connection_login_registers_mailbox_listener_snapshot(tmp_path, monkeypatch, test_client) -> None:
    reset_local_services(tmp_path, monkeypatch)
    import app.api.claw_auth as claw_auth_api_module

    claw_auth_api_module.claw_auth_service.dashboard = FakeDashboard()
    client = test_client

    verified = client.post("/api/connections/verify-code", json={"email": "user@163.com", "code": "123456"})
    listeners = client.get("/api/listeners")

    assert verified.status_code == 200
    assert listeners.status_code == 200
    assert listeners.json()["items"] == [{
        "connectionId": "user-163.com:workspace-1",
        "email": "root@claw.163.com",
        "status": "starting",
        "connected": False,
        "retry": 0,
        "error": None,
    }]


def test_connection_login_disconnects_duplicate_legacy_identity(tmp_path, monkeypatch, test_client) -> None:
    _repository, claw_repository = reset_local_services(tmp_path, monkeypatch)
    import app.api.claw_auth as claw_auth_api_module

    claw_auth_api_module.claw_auth_service.dashboard = FakeDashboard()
    claw_repository.upsert_connection({
        "id": "legacy",
        "label": "user@163.com",
        "user_email": "user@163.com",
        "workspace_id": "workspace-1",
        "workspace_name": "Workspace",
        "parent_mailbox_id": "mailbox-root",
        "root_prefix": "root",
        "domain": "claw.163.com",
        "api_key": "old-api-key-1234567890",
        "dashboard_cookie": "old-cookie",
        "status": "active",
    })
    client = test_client

    verified = client.post("/api/connections/verify-code", json={"email": "user@163.com", "code": "123456"})
    listed = client.get("/api/connections")

    assert verified.status_code == 200
    assert claw_repository.get_connection("legacy")["status"] == "disconnected"
    assert [item["id"] for item in listed.json()["items"]] == ["user-163.com:workspace-1"]


def test_delete_connection_removes_local_cache_and_listener(tmp_path, monkeypatch, test_client) -> None:
    _repository, claw_repository = reset_local_services(tmp_path, monkeypatch)
    import app.api.claw_auth as claw_auth_api_module
    import app.db.mail_repository as mail_repository_module

    claw_auth_api_module.claw_auth_service.dashboard = FakeDashboard()
    client = test_client

    verified = client.post("/api/connections/verify-code", json={"email": "user@163.com", "code": "123456"})
    connection_id = verified.json()["connection"]["id"]
    mail_repository_module.mail_repository.save_mail({
        "connection_id": connection_id,
        "provider_mail_id": "remote-mail-1",
        "mailbox_email": "root@claw.163.com",
        "source": "sender@example.com",
        "address": "root@claw.163.com",
        "subject": "hello",
        "text": "hello",
        "html": None,
        "raw_json": "{}",
        "attachments": [],
    })

    deleted = client.post(f"/api/connections/{connection_id}/delete")
    listed = client.get("/api/connections")
    mailboxes = client.get("/api/mailboxes")
    mails = client.get("/api/mails")
    listeners = client.get("/api/listeners")

    assert deleted.status_code == 200
    assert deleted.json() == {"success": True}
    assert claw_repository.get_connection(connection_id) is None
    assert listed.json()["items"] == []
    assert mailboxes.json()["items"] == []
    assert mails.json()["items"] == []
    assert listeners.json()["items"] == []
