import importlib
import json

from app.main import app


class FakeDashboard:
    def __init__(self) -> None:
        self.created_suffixes: list[tuple[str, str | None]] = []
        self.updated_settings: list[tuple[str, dict, str | None]] = []
        self.deleted_mailboxes: list[tuple[str, str | None]] = []
        self.remote_mailboxes: list[dict] = []

    def list_mailboxes(self, connection_id: str | None = None) -> list[dict]:
        return self.remote_mailboxes

    def create_mailbox(self, suffix: str, connection_id: str | None = None) -> dict:
        self.created_suffixes.append((suffix, connection_id))
        return {
            "id": f"remote-{suffix}",
            "email": f"{suffix}@claw.163.com",
            "prefix": suffix,
            "status": "active",
        }

    def update_mailbox_comm_settings(
        self,
        mailbox_id: str,
        payload: dict,
        connection_id: str | None = None,
    ) -> None:
        self.updated_settings.append((mailbox_id, payload, connection_id))

    def delete_mailbox(self, mailbox_id: str, connection_id: str | None = None) -> None:
        self.deleted_mailboxes.append((mailbox_id, connection_id))


class FakeMailClient:
    def __init__(self) -> None:
        self.deleted_mails: list[tuple[str, str, str | None]] = []
        self.delete_error: Exception | None = None

    def delete_mail(self, mailbox_email: str, provider_mail_id: str, connection_id: str | None = None) -> None:
        if self.delete_error:
            raise self.delete_error
        self.deleted_mails.append((mailbox_email, provider_mail_id, connection_id))


def reset_mail_services(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'mail.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    import app.core.config as config_module
    import app.db.mail_repository as mail_repository_module
    import app.services.listeners as listeners_module
    import app.services.mailboxes as mailboxes_service_module
    import app.services.mails as mails_service_module

    config_module.get_settings.cache_clear()
    importlib.reload(mail_repository_module)
    importlib.reload(listeners_module)
    importlib.reload(mailboxes_service_module)
    importlib.reload(mails_service_module)
    import app.api.events as events_api_module
    import app.api.mailboxes as mailboxes_api_module
    import app.api.mails as mails_api_module

    events_api_module.listener_manager = listeners_module.listener_manager
    events_api_module.listener_manager.worker_enabled = False
    mailboxes_api_module.mailbox_service = mailboxes_service_module.mailbox_service
    mails_api_module.mail_service = mails_service_module.mail_service
    return mail_repository_module.mail_repository, mailboxes_service_module.mailbox_service


def test_mailbox_local_crud_and_comm_settings(tmp_path, monkeypatch, test_client) -> None:
    repository, mailbox_service = reset_mail_services(tmp_path, monkeypatch)
    dashboard = FakeDashboard()
    mailbox_service.dashboard = dashboard
    client = test_client

    created = client.post("/api/mailboxes", json={"connectionId": "conn-1", "suffix": "demo"})
    listed = client.get("/api/mailboxes")
    updated = client.post(f"/api/mailboxes/{created.json()['id']}/comm-settings", json={
        "commLevel": 1,
    })
    deleted = client.delete(f"/api/mailboxes/{created.json()['id']}")
    listed_after_delete = client.get("/api/mailboxes")

    assert created.status_code == 201
    assert created.json()["email"] == "demo@claw.163.com"
    assert created.json()["id"] == "conn-1:remote-demo"
    assert created.json()["comm_level"] == 2
    assert dashboard.created_suffixes == [("demo", "conn-1")]
    assert dashboard.updated_settings[0] == ("remote-demo", {
        "commLevel": 2,
        "extReceiveType": 1,
        "extSendType": 1,
    }, "conn-1")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["email"] == "demo@claw.163.com"
    assert updated.status_code == 200
    assert updated.json()["comm_level"] == 1
    assert updated.json()["ext_receive_type"] is None
    assert dashboard.updated_settings[1] == ("remote-demo", {"commLevel": 1}, "conn-1")
    assert deleted.status_code == 200
    assert dashboard.deleted_mailboxes == [("remote-demo", "conn-1")]
    assert listed_after_delete.json()["items"] == []
    assert repository.get_mailbox(created.json()["id"])["status"] == "deleted"


def test_mailbox_sync_imports_remote_rows(tmp_path, monkeypatch, test_client) -> None:
    repository, mailbox_service = reset_mail_services(tmp_path, monkeypatch)
    dashboard = FakeDashboard()
    dashboard.remote_mailboxes = [{
        "id": "remote-demo",
        "email": "demo@claw.163.com",
        "prefix": "demo",
        "display_name": "Demo",
        "status": "active",
        "comm_level": 2,
        "ext_receive_type": 1,
        "ext_send_type": 1,
    }]
    mailbox_service.dashboard = dashboard
    client = test_client

    response = client.get("/api/mailboxes?connectionId=conn-1&sync=true")

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "conn-1:remote-demo"
    assert response.json()["items"][0]["display_name"] == "Demo"
    assert repository.get_mailbox("conn-1:remote-demo")["email"] == "demo@claw.163.com"


def test_mail_list_detail_and_remote_delete(tmp_path, monkeypatch, test_client) -> None:
    repository, _mailbox_service = reset_mail_services(tmp_path, monkeypatch)
    fake_mail_client = FakeMailClient()
    import app.api.mails as mails_api_module

    mails_api_module.mail_service.mail_client = fake_mail_client
    repository.save_mail({
        "provider_mail_id": "remote-1",
        "mailbox_email": "demo@claw.163.com",
        "source": "sender@example.com",
        "address": "demo@claw.163.com",
        "subject": "hello",
        "text": "body",
        "html": None,
        "raw_json": json.dumps({"id": "remote-1", "subject": "hello"}),
        "has_attachments": True,
        "attachments": [{
            "provider_part_id": "part-1",
            "filename": "a.txt",
            "content_type": "text/plain",
            "size": 3,
        }],
    })
    client = test_client

    listed = client.get("/api/mails?mailbox=demo@claw.163.com")
    detail = client.get(f"/api/mails/{listed.json()['items'][0]['id']}")
    deleted = client.delete(f"/api/mails/{listed.json()['items'][0]['id']}")
    listed_after_delete = client.get("/api/mails")

    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["total"] == 1
    assert listed.json()["limit"] == 50
    assert listed.json()["offset"] == 0
    assert listed.json()["items"][0]["subject"] == "hello"
    assert detail.status_code == 200
    assert detail.json()["parsed"] == {"id": "remote-1", "subject": "hello"}
    assert detail.json()["attachments"][0]["provider_part_id"] == "part-1"
    assert detail.json()["read_at"] is not None
    assert deleted.status_code == 200
    assert deleted.json() == {"success": True}
    assert listed_after_delete.json()["count"] == 0
    assert fake_mail_client.deleted_mails == [("demo@claw.163.com", "remote-1", "legacy")]


def test_mail_list_supports_pagination_and_keyword(tmp_path, monkeypatch, test_client) -> None:
    repository, _mailbox_service = reset_mail_services(tmp_path, monkeypatch)
    repository.save_mail({
        "provider_mail_id": "remote-1",
        "mailbox_email": "demo@claw.163.com",
        "source": "sender@example.com",
        "address": "demo@claw.163.com",
        "subject": "alpha subject",
        "text": "first body",
        "raw_json": "{}",
    })
    repository.save_mail({
        "provider_mail_id": "remote-2",
        "mailbox_email": "demo@claw.163.com",
        "source": "other@example.com",
        "address": "demo@claw.163.com",
        "subject": "beta subject",
        "text": "second body",
        "raw_json": "{}",
    })
    client = test_client

    paged = client.get("/api/mails?mailbox=demo@claw.163.com&limit=1&offset=1")
    searched = client.get("/api/mails?keyword=alpha")

    assert paged.status_code == 200
    assert paged.json()["total"] == 2
    assert paged.json()["count"] == 2
    assert paged.json()["limit"] == 1
    assert paged.json()["offset"] == 1
    assert len(paged.json()["items"]) == 1
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["subject"] == "alpha subject"


def test_clear_mails_deletes_filtered_remote_and_local_rows(tmp_path, monkeypatch, test_client) -> None:
    repository, _mailbox_service = reset_mail_services(tmp_path, monkeypatch)
    fake_mail_client = FakeMailClient()
    import app.api.mails as mails_api_module

    mails_api_module.mail_service.mail_client = fake_mail_client
    repository.save_mail({
        "provider_mail_id": "remote-1",
        "mailbox_email": "one@claw.163.com",
        "raw_json": "{}",
    })
    repository.save_mail({
        "provider_mail_id": "remote-2",
        "mailbox_email": "two@claw.163.com",
        "raw_json": "{}",
    })
    client = test_client

    response = client.delete("/api/mails?mailbox=one@claw.163.com")

    assert response.status_code == 200
    assert response.json() == {"success": True, "deleted": 1, "failed": 0, "errors": []}
    assert client.get("/api/mails").json()["count"] == 1
    assert fake_mail_client.deleted_mails == [("one@claw.163.com", "remote-1", "legacy")]


def test_clear_mails_keeps_local_delete_when_remote_connection_missing(tmp_path, monkeypatch, test_client) -> None:
    repository, _mailbox_service = reset_mail_services(tmp_path, monkeypatch)
    fake_mail_client = FakeMailClient()
    fake_mail_client.delete_error = ValueError("CLAW_API_KEY is required for mail operations; connect Claw first")
    import app.api.mails as mails_api_module

    mails_api_module.mail_service.mail_client = fake_mail_client
    repository.save_mail({
        "connection_id": "missing-connection",
        "provider_mail_id": "remote-1",
        "mailbox_email": "gone@claw.163.com",
        "raw_json": "{}",
    })
    client = test_client

    response = client.delete("/api/mails?mailbox=gone@claw.163.com")

    assert response.status_code == 200
    assert response.json() == {"success": True, "deleted": 1, "failed": 0, "errors": []}
    assert client.get("/api/mails").json()["count"] == 0


def test_create_mailbox_registers_listener_snapshot(tmp_path, monkeypatch, test_client) -> None:
    _repository, mailbox_service = reset_mail_services(tmp_path, monkeypatch)
    mailbox_service.dashboard = FakeDashboard()
    client = test_client

    created = client.post("/api/mailboxes", json={"connectionId": "conn-1", "suffix": "demo"})
    listeners = client.get("/api/listeners")

    assert created.status_code == 201
    assert listeners.status_code == 200
    assert listeners.json()["items"][0]["connectionId"] == "conn-1"
    assert listeners.json()["items"][0]["email"] == "demo@claw.163.com"
    assert listeners.json()["items"][0]["status"] == "starting"
