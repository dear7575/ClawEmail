import importlib


class FakeMailClient:
    def __init__(self) -> None:
        self.read_calls: list[tuple[str, str, bool]] = []

    def list_inbox_message_ids(self, mailbox_email: str, max_messages: int, connection_id: str | None = None) -> list[str]:
        return ["old-login", "new-code", "should-not-read"]

    def read_mail(
        self,
        mailbox_email: str,
        provider_mail_id: str,
        connection_id: str | None = None,
        mark_read: bool = False,
    ) -> dict:
        self.read_calls.append((mailbox_email, provider_mail_id, mark_read))
        if provider_mail_id == "should-not-read":
            raise AssertionError("找到验证码后不应该继续读取旧邮件")
        if provider_mail_id == "old-login":
            return {
                "id": provider_mail_id,
                "to": ["other@duck.com"],
                "subject": "Your temporary OpenAI verification code",
                "date": "2026-05-15T10:12:22+00:00",
                "text": {"content": "111111"},
            }
        return {
            "id": provider_mail_id,
            "to": ["target@duck.com"],
            "subject": "Your temporary OpenAI verification code",
            "date": "2026-05-15T10:38:24+00:00",
            "text": {"content": "222222"},
        }


class FakeMailRepository:
    def __init__(self) -> None:
        self.saved_provider_ids: list[str] = []
        self.marked_read_ids: list[int] = []

    def save_mail(self, input_value: dict) -> dict:
        self.saved_provider_ids.append(input_value["provider_mail_id"])
        return {"id": len(self.saved_provider_ids)}

    def get_mail_by_provider_id(
        self,
        mailbox_email: str,
        provider_mail_id: str,
        connection_id: str | None = None,
    ) -> dict | None:
        return {"id": 99, "provider_mail_id": provider_mail_id}

    def mark_mail_read(self, mail_id: int) -> dict:
        self.marked_read_ids.append(mail_id)
        return {"id": mail_id}


def test_openai_otp_returns_immediately_after_first_valid_candidate() -> None:
    import app.services.openai_auth as openai_auth_module

    importlib.reload(openai_auth_module)
    mail_client = FakeMailClient()
    mail_repository = FakeMailRepository()
    service = openai_auth_module.OpenAiAuthService(
        mail_repo=mail_repository,
        mail_client=mail_client,
    )

    candidate = service.read_latest_verification_code(
        {
            "email": "inbox@claw.163.com",
            "connection_id": "connection-1",
        },
        "target@duck.com",
        0,
        set(),
    )

    assert candidate is not None
    assert candidate.code == "222222"
    assert candidate.provider_mail_id == "new-code"
    assert [call[1] for call in mail_client.read_calls] == ["old-login", "new-code", "new-code"]
    assert mail_client.read_calls[-1][2] is True
    assert mail_repository.marked_read_ids == [99]


class DatedFakeMailClient(FakeMailClient):
    def __init__(self) -> None:
        super().__init__()
        self.list_limits: list[int] = []

    def list_inbox_messages(self, mailbox_email: str, max_messages: int, connection_id: str | None = None) -> list[dict]:
        self.list_limits.append(max_messages)
        return [
            {"id": "newer-wrong-recipient", "sentDate": "2026-05-15T10:38:24+00:00"},
            {"id": "too-old", "sentDate": "2026-05-15T10:20:00+00:00"},
            {"id": "must-not-read", "sentDate": "2026-05-15T10:19:00+00:00"},
        ]

    def read_mail(
        self,
        mailbox_email: str,
        provider_mail_id: str,
        connection_id: str | None = None,
        mark_read: bool = False,
    ) -> dict:
        if provider_mail_id != "newer-wrong-recipient":
            raise AssertionError("遇到早于本次验证码发送时间的邮件后不应该继续读取历史邮件")
        self.read_calls.append((mailbox_email, provider_mail_id, mark_read))
        return {
            "id": provider_mail_id,
            "to": ["other@duck.com"],
            "subject": "Your temporary OpenAI verification code",
            "date": "2026-05-15T10:38:24+00:00",
            "text": {"content": "333333"},
        }


def test_openai_otp_stops_scanning_when_summary_is_older_than_request_time() -> None:
    import app.services.openai_auth as openai_auth_module

    importlib.reload(openai_auth_module)
    mail_client = DatedFakeMailClient()
    service = openai_auth_module.OpenAiAuthService(
        mail_repo=FakeMailRepository(),
        mail_client=mail_client,
    )

    candidate = service.read_latest_verification_code(
        {
            "email": "inbox@claw.163.com",
            "connection_id": "connection-1",
        },
        "target@duck.com",
        1778841300000,
        set(),
    )

    assert candidate is None
    assert mail_client.list_limits == [100]
    assert [call[1] for call in mail_client.read_calls] == ["newer-wrong-recipient"]


class UnreadFakeMailClient(DatedFakeMailClient):
    def list_inbox_messages(self, mailbox_email: str, max_messages: int, connection_id: str | None = None) -> list[dict]:
        return [
            {"id": "read-new", "sentDate": "2026-05-15T10:38:24+00:00", "isRead": True},
            {"id": "unread-new", "sentDate": "2026-05-15T10:38:25+00:00", "isRead": False},
        ]

    def read_mail(
        self,
        mailbox_email: str,
        provider_mail_id: str,
        connection_id: str | None = None,
        mark_read: bool = False,
    ) -> dict:
        if provider_mail_id == "read-new":
            raise AssertionError("邮件摘要标记已读时不应该读取正文")
        self.read_calls.append((mailbox_email, provider_mail_id, mark_read))
        return {
            "id": provider_mail_id,
            "to": ["target@duck.com"],
            "subject": "Your temporary OpenAI verification code",
            "date": "2026-05-15T10:38:25+00:00",
            "text": {"content": "444444"},
        }


def test_openai_otp_prefers_unread_mail_when_summary_has_read_flag() -> None:
    import app.services.openai_auth as openai_auth_module

    importlib.reload(openai_auth_module)
    mail_client = UnreadFakeMailClient()
    service = openai_auth_module.OpenAiAuthService(
        mail_repo=FakeMailRepository(),
        mail_client=mail_client,
    )

    candidate = service.read_latest_verification_code(
        {
            "email": "inbox@claw.163.com",
            "connection_id": "connection-1",
        },
        "target@duck.com",
        1778841300000,
        set(),
    )

    assert candidate is not None
    assert candidate.code == "444444"
    assert [call[1] for call in mail_client.read_calls] == ["unread-new", "unread-new"]


class EmptyMailClient(FakeMailClient):
    def list_inbox_messages(self, mailbox_email: str, max_messages: int, connection_id: str | None = None) -> list[dict]:
        return []


def test_openai_otp_uses_fast_poll_interval_at_wait_start(monkeypatch) -> None:
    import app.services.openai_auth as openai_auth_module

    importlib.reload(openai_auth_module)
    sleep_calls: list[float] = []
    ticks = iter([0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 61.0])

    monkeypatch.setattr(openai_auth_module.time, "time", lambda: next(ticks))
    monkeypatch.setattr(openai_auth_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    service = openai_auth_module.OpenAiAuthService(
        mail_repo=FakeMailRepository(),
        mail_client=EmptyMailClient(),
    )

    try:
        service.wait_for_verification_code(
            {
                "email": "inbox@claw.163.com",
                "connection_id": "connection-1",
            },
            "target@duck.com",
            1778841300000,
            60_000,
            set(),
        )
    except RuntimeError:
        pass

    assert sleep_calls == [2]
