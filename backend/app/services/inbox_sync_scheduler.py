import logging
import threading

from app.services.listener_settings import (
    InboxSyncInterval,
    ListenerSettingsService,
    listener_settings_service,
)
from app.services.mails import MailService, mail_service


logger = logging.getLogger(__name__)
INBOX_SYNC_INTERVAL_SECONDS: dict[InboxSyncInterval, int | None] = {
    "manual": None,
    "30": 30,
    "60": 60,
    "300": 300,
}


class InboxSyncScheduler:
    """服务端收件箱后台同步调度器。"""

    def __init__(
        self,
        mail_service_value: MailService = mail_service,
        settings_service: ListenerSettingsService = listener_settings_service,
    ) -> None:
        """初始化后台线程状态。

        参数:
            mail_service_value: 邮件服务，用于复用现有远端同步和告警逻辑。
            settings_service: 设置服务，用于每轮读取最新同步频率。
        """

        self.mail_service = mail_service_value
        self.settings_service = settings_service
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.running = threading.Lock()
        self.lifecycle_lock = threading.Lock()

    def start(self) -> None:
        """启动收件箱后台同步线程。"""

        with self.lifecycle_lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, name="inbox-sync", daemon=True)
            self.thread.start()
            logger.info("收件箱后台同步调度器已启动")

    def shutdown(self) -> None:
        """停止收件箱后台同步线程。"""

        with self.lifecycle_lock:
            self.stop_event.set()
            self.wake_event.set()
            thread = self.thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        logger.info("收件箱后台同步调度器已停止")

    def reload_settings(self) -> None:
        """通知后台线程重新读取同步频率。"""

        self.wake_event.set()

    def _run(self) -> None:
        """按服务端设置循环等待并触发同步。"""

        while not self.stop_event.is_set():
            interval = self.current_interval_seconds()
            wait_seconds = 5 if interval is None else interval
            self.wake_event.wait(wait_seconds)
            if self.stop_event.is_set():
                break
            if self.wake_event.is_set():
                self.wake_event.clear()
                continue
            if interval is None:
                continue
            self.sync_once()

    def current_interval_seconds(self) -> int | None:
        """读取当前收件箱后台同步间隔。"""

        settings = self.settings_service.get()
        return INBOX_SYNC_INTERVAL_SECONDS.get(settings.inboxSyncInterval)

    def sync_once(self) -> None:
        """执行一轮全部邮箱同步，上一轮未结束时跳过本轮。"""

        if not self.running.acquire(blocking=False):
            logger.warning("跳过收件箱后台同步：上一轮仍在执行")
            return
        try:
            logger.info("开始收件箱后台同步")
            errors = self.mail_service.sync_all_mailbox_inboxes()
            logger.info("收件箱后台同步完成：failed=%s", len(errors))
        except Exception as exc:
            # 后台调度器不能因为单轮失败退出，具体邮箱失败由 MailService 记录。
            logger.warning("收件箱后台同步异常：error=%s", exc)
        finally:
            self.running.release()


inbox_sync_scheduler = InboxSyncScheduler()
