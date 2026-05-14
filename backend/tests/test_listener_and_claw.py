import importlib

from app.main import app


class FakeDashboard:
    def __init__(self) -> None:
        self.sent_codes: list[str] = []
        self.verified_codes: list[tuple[str, str, str | None]] = []

    def send_login_code(self, email: str) -> str:
        self.sent_codes.append(email)
        return "pending-cookie"

    def verify_login_code(self, email: str, code: str, pending_cookie: str | None = None) -> str:
        self.verified_codes.append((email, code, pending_cookie))
        return "dashboard-cookie"

    def get_auth_me(self, cookie: str) -> dict:
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
    import app.services.claw_auth as claw_auth_module

    config_module.get_settings.cache_clear()
    importlib.reload(settings_repository_module)
    importlib.reload(claw_repository_module)
    importlib.reload(mail_repository_module)
    importlib.reload(listener_settings_module)
    importlib.reload(claw_auth_module)
    import app.api.events as events_api_module
    import app.api.claw_auth as claw_auth_api_module
    import app.api.mailboxes as mailboxes_api_module

    events_api_module.listener_settings_service = listener_settings_module.listener_settings_service
    claw_auth_api_module.claw_auth_service = claw_auth_module.claw_auth_service
    mailboxes_api_module.mailbox_service.repository = mail_repository_module.mail_repository
    return settings_repository_module.settings_repository, claw_repository_module.claw_repository


def test_listener_settings_defaults_and_save(tmp_path, monkeypatch, test_client) -> None:
    repository, _claw_repository = reset_local_services(tmp_path, monkeypatch)
    client = test_client

    defaults = client.get("/api/listener-settings")
    saved = client.put("/api/listener-settings", json={
        "logMode": "verbose",
        "reconnectMode": "slow",
    })

    assert defaults.status_code == 200
    assert defaults.json() == {"logMode": "quiet", "reconnectMode": "standard"}
    assert saved.status_code == 200
    assert saved.json() == {"logMode": "verbose", "reconnectMode": "slow"}
    assert repository.get("listener.logMode") == "verbose"
    assert repository.get("listener.reconnectMode") == "slow"


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
