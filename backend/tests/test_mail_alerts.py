import importlib


class FakeTelegramService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[str] = []

    def send_message(self, text: str) -> None:
        if self.error:
            raise self.error
        self.messages.append(text)


class FakeSyncMailClient:
    def __init__(self, mail: dict) -> None:
        self.mail = mail
        self.repository = self

    def resolve_connection(self, connection_id: str | None = None) -> dict:
        return {"id": connection_id or "legacy", "api_key": "key"}

    def list_inbox_message_ids(
        self,
        mailbox_email: str,
        max_messages: int = 500,
        connection_id: str | None = None,
    ) -> list[str]:
        return [str(self.mail["id"])]

    def read_mail(
        self,
        mailbox_email: str,
        provider_mail_id: str,
        connection_id: str | None = None,
        mark_read: bool = False,
    ) -> dict:
        return self.mail


def reset_mail_alert_services(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'mail-alerts.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    import app.core.config as config_module
    import app.db.mail_repository as mail_repository_module
    import app.services.mail_alerts as mail_alerts_module
    import app.services.mails as mails_service_module

    config_module.get_settings.cache_clear()
    importlib.reload(mail_repository_module)
    importlib.reload(mail_alerts_module)
    importlib.reload(mails_service_module)
    return mail_repository_module.mail_repository, mail_alerts_module, mails_service_module


def test_new_mail_notification_sends_for_any_mail_and_keeps_unread(tmp_path, monkeypatch) -> None:
    repository, mail_alerts_module, _mails_service_module = reset_mail_alert_services(tmp_path, monkeypatch)
    telegram = FakeTelegramService()
    service = mail_alerts_module.MailAlertService(telegram_service_value=telegram)
    mail = repository.save_mail({
        "provider_mail_id": "remote-1",
        "mailbox_email": "demo@claw.163.com",
        "source": "sender@example.com",
        "subject": "普通邮件主题",
        "text": "status=account_deactivated",
        "raw_json": "{}",
    })

    result = service.notify_new_mail(mail)
    refreshed = repository.get_mail(mail["id"])

    assert result == {"sent": True}
    assert len(telegram.messages) == 1
    assert "收到新邮件" in telegram.messages[0]
    assert "普通邮件主题" in telegram.messages[0]
    assert "demo@claw.163.com" in telegram.messages[0]
    assert refreshed["read_at"] is None


def test_new_mail_notification_failure_does_not_raise_or_mark_read(tmp_path, monkeypatch) -> None:
    repository, mail_alerts_module, _mails_service_module = reset_mail_alert_services(tmp_path, monkeypatch)
    service = mail_alerts_module.MailAlertService(
        telegram_service_value=FakeTelegramService(RuntimeError("tg down")),
    )
    mail = repository.save_mail({
        "provider_mail_id": "remote-2",
        "mailbox_email": "demo@claw.163.com",
        "subject": "普通邮件",
        "raw_json": "{}",
    })

    result = service.notify_new_mail(mail)
    refreshed = repository.get_mail(mail["id"])

    assert result["sent"] is False
    assert "tg down" in result["error"]
    assert refreshed["read_at"] is None


def test_sync_new_mail_sends_notification_and_broadcasts_event(tmp_path, monkeypatch) -> None:
    repository, mail_alerts_module, mails_service_module = reset_mail_alert_services(tmp_path, monkeypatch)
    import app.services.sse as sse_module

    telegram = FakeTelegramService()
    events: list[tuple[str, dict]] = []

    def capture_event(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    monkeypatch.setattr(sse_module.sse_hub, "broadcast", capture_event)
    alert_service = mail_alerts_module.MailAlertService(telegram_service_value=telegram)
    service = mails_service_module.MailService(
        repository=repository,
        mail_client=FakeSyncMailClient({
            "id": "remote-3",
            "from": ["sender@example.com"],
            "to": ["demo@claw.163.com"],
            "subject": "普通邮件",
            "text": {"content": "body"},
            "date": "2026-06-02T00:00:00",
        }),
        alert_service=alert_service,
    )
    repository.upsert_mailbox({
        "id": "remote-demo",
        "connection_id": "conn-1",
        "provider_mailbox_id": "remote-demo",
        "email": "demo@claw.163.com",
        "prefix": "demo",
        "status": "active",
    })

    service.sync_mailbox_inbox("conn-1", "demo@claw.163.com")
    saved = repository.get_mail_by_provider_id("demo@claw.163.com", "remote-3", "conn-1")

    assert len(telegram.messages) == 1
    assert "普通邮件" in telegram.messages[0]
    assert saved["read_at"] is None
    assert events == [("mail", {
        "connectionId": "conn-1",
        "mailboxEmail": "demo@claw.163.com",
        "id": saved["id"],
        "providerMailId": "remote-3",
    })]


def test_sync_existing_mail_does_not_send_duplicate_notification(tmp_path, monkeypatch) -> None:
    repository, mail_alerts_module, mails_service_module = reset_mail_alert_services(tmp_path, monkeypatch)
    telegram = FakeTelegramService()
    service = mails_service_module.MailService(
        repository=repository,
        mail_client=FakeSyncMailClient({
            "id": "remote-4",
            "from": ["sender@example.com"],
            "to": ["demo@claw.163.com"],
            "subject": "普通邮件",
        }),
        alert_service=mail_alerts_module.MailAlertService(telegram_service_value=telegram),
    )
    repository.upsert_mailbox({
        "id": "remote-demo",
        "connection_id": "conn-1",
        "provider_mailbox_id": "remote-demo",
        "email": "demo@claw.163.com",
        "prefix": "demo",
        "status": "active",
    })
    repository.save_mail({
        "connection_id": "conn-1",
        "provider_mail_id": "remote-4",
        "mailbox_email": "demo@claw.163.com",
        "subject": "普通邮件",
        "raw_json": "{}",
    })

    service.sync_mailbox_inbox("conn-1", "demo@claw.163.com")

    assert telegram.messages == []
