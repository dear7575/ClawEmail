from app.services.inbox_sync_scheduler import InboxSyncScheduler
from app.services.listener_settings import ListenerSettings


class FakeSettingsService:
    """测试用设置服务，按固定值返回后台刷新配置。"""

    def __init__(self, interval: str = "manual") -> None:
        """初始化测试配置。"""

        self.interval = interval

    def get(self) -> ListenerSettings:
        """返回当前测试配置。"""

        return ListenerSettings(inboxSyncInterval=self.interval)


class FakeMailService:
    """测试用邮件服务，记录后台同步调用次数。"""

    def __init__(self, error: Exception | None = None) -> None:
        """初始化测试同步服务。"""

        self.error = error
        self.calls = 0

    def sync_all_mailbox_inboxes(self):
        """模拟同步全部邮箱收件箱。"""

        self.calls += 1
        if self.error:
            raise self.error
        return []


def test_inbox_sync_scheduler_reads_manual_interval() -> None:
    service = FakeMailService()
    scheduler = InboxSyncScheduler(service, FakeSettingsService("manual"))

    assert scheduler.current_interval_seconds() is None


def test_inbox_sync_scheduler_reads_enabled_interval() -> None:
    service = FakeMailService()
    scheduler = InboxSyncScheduler(service, FakeSettingsService("60"))

    assert scheduler.current_interval_seconds() == 60


def test_inbox_sync_scheduler_runs_mail_sync_once() -> None:
    service = FakeMailService()
    scheduler = InboxSyncScheduler(service, FakeSettingsService("30"))

    scheduler.sync_once()

    assert service.calls == 1


def test_inbox_sync_scheduler_reload_settings_wakes_worker() -> None:
    service = FakeMailService()
    scheduler = InboxSyncScheduler(service, FakeSettingsService("30"))

    scheduler.reload_settings()

    assert scheduler.wake_event.is_set()


def test_inbox_sync_scheduler_keeps_running_after_sync_error() -> None:
    service = FakeMailService(RuntimeError("network down"))
    scheduler = InboxSyncScheduler(service, FakeSettingsService("30"))

    scheduler.sync_once()

    assert service.calls == 1
