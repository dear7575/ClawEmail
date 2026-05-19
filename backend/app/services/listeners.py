import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any

from app.db.claw_repository import ClawRepository, claw_repository
from app.db.mail_repository import MailRepository, mail_repository
from app.services.claw_im import (
    ClawImWebSocket,
    HEARTBEAT_SECONDS,
    MAX_MISSED_PONGS,
    RecvPacket,
    decode_packet,
    encode_disconnect,
    encode_ping,
    encode_recvack,
)
from app.services.claw_mail import ClawMailClient, attachment_list, claw_mail_client
from app.services.listener_settings import listener_settings_service
from app.services.sse import sse_hub


logger = logging.getLogger(__name__)
RECONNECT_BACKOFF_SECONDS = {
    "standard": [1, 2, 4, 8, 16, 30],
    "slow": [10, 30, 60, 120, 300],
}


@dataclass
class ListenerState:
    """单个邮箱监听器的运行状态快照。"""

    connection_id: str
    email: str
    status: str = "starting"
    connected: bool = False
    retry: int = 0
    error: str | None = None


class ListenerManager:
    """Claw 邮箱 WebSocket 后台监听器管理器。"""

    def __init__(
        self,
        connection_repository: ClawRepository = claw_repository,
        mail_repository: MailRepository = mail_repository,
        mail_client: ClawMailClient = claw_mail_client,
    ) -> None:
        """初始化进程内监听器状态容器和后台事件循环。"""

        self.connection_repository = connection_repository
        self.mail_repository = mail_repository
        self.mail_client = mail_client
        self.listeners: dict[str, ListenerState] = {}
        self.tasks: dict[str, asyncio.Future] = {}
        self.stop_events: dict[str, asyncio.Event] = {}
        self.worker_enabled = True
        self.lock = threading.RLock()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, name="claw-listeners", daemon=True)
        self.thread.start()

    def _run_loop(self) -> None:
        """运行监听器专用 asyncio 事件循环。"""

        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    @staticmethod
    def key(connection_id: str, email: str) -> str:
        """生成监听器状态字典键。"""

        return f"{connection_id}:{email.strip().lower()}"

    def snapshot(self) -> list[dict]:
        """返回前端可展示的监听器状态列表。"""

        with self.lock:
            items = list(self.listeners.values())
        return [
            {
                "connectionId": item.connection_id,
                "email": item.email,
                "status": item.status,
                "connected": item.connected,
                "retry": item.retry,
                "error": item.error,
            }
            for item in items
        ]

    def sync_mailboxes(self, connection_id: str, mailboxes: list[dict[str, Any]]) -> None:
        """按邮箱列表同步并启动监听器。

        参数:
            connection_id: Claw 连接 ID。
            mailboxes: 已同步到本地或 Dashboard 返回的邮箱列表。
        """

        expected_keys: set[str] = set()
        for mailbox in mailboxes:
            if (mailbox.get("status") or "active") != "active" or not mailbox.get("email"):
                continue
            email = str(mailbox["email"]).strip().lower()
            expected_keys.add(self.key(connection_id, email))
            self.start_mailbox_listener(connection_id, email)
        for key, item in list(self.listeners.items()):
            if item.connection_id == connection_id and key not in expected_keys:
                self.mark_stopped(item.email, item.connection_id)

    def start_mailbox_listener(self, connection_id: str, email: str) -> None:
        """启动单个邮箱的后台 WebSocket 监听器。"""

        key = self.key(connection_id, email)
        with self.lock:
            existing = self.listeners.get(key)
            task = self.tasks.get(key)
            if existing and task and not task.done():
                return
            state = existing or ListenerState(connection_id=connection_id, email=email)
            state.status = "starting"
            state.connected = False
            state.error = None
            self.listeners[key] = state
            if not self.worker_enabled:
                return

        stop_event_future = asyncio.run_coroutine_threadsafe(self._new_stop_event(), self.loop)
        stop_event = stop_event_future.result(timeout=5)
        future = asyncio.run_coroutine_threadsafe(self._listen_forever(state, stop_event), self.loop)
        with self.lock:
            self.stop_events[key] = stop_event
            self.tasks[key] = future

    async def _new_stop_event(self) -> asyncio.Event:
        """在监听线程的事件循环中创建停止事件。"""

        return asyncio.Event()

    def mark_connection_stopped(self, connection_id: str) -> None:
        """停止并移除指定连接下的全部监听器状态。"""

        for item in list(self.snapshot()):
            if item["connectionId"] == connection_id:
                self.mark_stopped(item["email"], connection_id)

    def mark_stopped(self, email: str, connection_id: str = "legacy") -> None:
        """停止并移除指定邮箱监听器。"""

        key = self.key(connection_id, email)
        with self.lock:
            stop_event = self.stop_events.pop(key, None)
            future = self.tasks.pop(key, None)
            self.listeners.pop(key, None)
        if stop_event:
            self.loop.call_soon_threadsafe(stop_event.set)
        if future:
            future.cancel()

    def shutdown(self) -> None:
        """停止全部监听任务和后台事件循环，主要供测试进程清理。"""

        for item in list(self.snapshot()):
            self.mark_stopped(item["email"], item["connectionId"])
        self.loop.call_soon_threadsafe(self.loop.stop)

    async def _listen_forever(self, state: ListenerState, stop_event: asyncio.Event) -> None:
        """持续连接 IM WebSocket，断线后按配置退避重连。"""

        while not stop_event.is_set():
            try:
                await self._connect_once(state, stop_event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_error(state, str(exc))
            if stop_event.is_set():
                break
            delay = self._next_backoff(state.retry)
            state.retry += 1
            await self._sleep_or_stop(delay, stop_event)

    async def _connect_once(self, state: ListenerState, stop_event: asyncio.Event) -> None:
        """建立一次 WebSocket 连接并处理该连接生命周期。"""

        connection = self.connection_repository.get_connection(state.connection_id)
        api_key = connection.get("api_key") if connection else None
        if not api_key:
            raise RuntimeError("Claw API Key 不存在，无法启动邮箱监听")
        client = ClawImWebSocket(state.email, str(api_key))
        websocket = await client.connect()
        state.status = "running"
        state.connected = True
        state.retry = 0
        state.error = None
        missed_pongs = 0
        logger.info("Claw 邮箱监听已连接：connection=%s email=%s", state.connection_id, state.email)
        heartbeat_task = asyncio.create_task(self._heartbeat(websocket, stop_event))
        try:
            while not stop_event.is_set():
                raw = await asyncio.wait_for(websocket.recv(), timeout=HEARTBEAT_SECONDS + 10)
                packet = decode_packet(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
                if packet == "pong":
                    missed_pongs = 0
                    continue
                if isinstance(packet, RecvPacket):
                    await websocket.send(encode_recvack(packet.message_id, packet.message_seq))
                    push = client.decode_push(packet)
                    if push:
                        await self._persist_mail_push(state, push["mailId"])
                    continue
                if isinstance(packet, str) and packet.startswith("unknown:"):
                    continue
                raise RuntimeError(f"Claw IM 连接断开：{packet}")
        except asyncio.TimeoutError as exc:
            missed_pongs += 1
            if missed_pongs > MAX_MISSED_PONGS:
                raise RuntimeError("Claw IM 心跳超时") from exc
        finally:
            heartbeat_task.cancel()
            await self._close_websocket(websocket)
            state.connected = False
            if state.status == "running":
                state.status = "closed"

    async def _heartbeat(self, websocket, stop_event: asyncio.Event) -> None:
        """按固定间隔发送 IM 心跳包。"""

        while not stop_event.is_set():
            await self._sleep_or_stop(HEARTBEAT_SECONDS, stop_event)
            if stop_event.is_set():
                break
            await websocket.send(encode_ping())

    async def _persist_mail_push(self, state: ListenerState, provider_mail_id: str) -> None:
        """读取新邮件详情、写入本地数据库并推送 SSE。"""

        mail = await asyncio.to_thread(
            self.mail_client.read_mail,
            state.email,
            provider_mail_id,
            state.connection_id,
            False,
        )
        saved = await asyncio.to_thread(
            self.mail_repository.save_mail,
            {
                "connection_id": state.connection_id,
                "provider_mail_id": provider_mail_id,
                "mailbox_email": state.email,
                "source": next(iter(mail.get("from") or []), None),
                "address": next(iter(mail.get("to") or []), state.email),
                "subject": mail.get("subject"),
                "text": mail.get("text", {}).get("content") if isinstance(mail.get("text"), dict) else None,
                "html": mail.get("html", {}).get("content") if isinstance(mail.get("html"), dict) else None,
                "raw_json": self.mail_client_json(mail),
                "header_raw": mail.get("headerRaw"),
                "has_attachments": bool(mail.get("attachments")),
                "received_at": mail.get("date"),
                "attachments": attachment_list(mail),
            },
        )
        sse_hub.broadcast("mail", {
            "connectionId": state.connection_id,
            "mailboxEmail": state.email,
            "id": saved["id"],
            "providerMailId": provider_mail_id,
        })

    @staticmethod
    def mail_client_json(mail: dict[str, Any]) -> str:
        """序列化远端邮件原始结构，避免循环导入 json 工具。"""

        import json

        return json.dumps(mail, ensure_ascii=False)

    def _mark_error(self, state: ListenerState, message: str) -> None:
        """记录监听器错误状态。"""

        state.status = "error"
        state.connected = False
        state.error = message
        logger.warning("Claw 邮箱监听异常：connection=%s email=%s error=%s", state.connection_id, state.email, message)

    @staticmethod
    async def _sleep_or_stop(delay: int | float, stop_event: asyncio.Event) -> None:
        """等待指定时间，期间允许停止事件打断。"""

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return

    @staticmethod
    async def _close_websocket(websocket) -> None:
        """发送断开包并关闭 WebSocket。"""

        try:
            await websocket.send(encode_disconnect())
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass

    @staticmethod
    def _next_backoff(retry: int) -> int:
        """根据监听器设置计算下一次重连等待时间。"""

        mode = listener_settings_service.get().reconnectMode
        backoff = RECONNECT_BACKOFF_SECONDS.get(mode, RECONNECT_BACKOFF_SECONDS["standard"])
        return backoff[min(retry, len(backoff) - 1)]


listener_manager = ListenerManager()
