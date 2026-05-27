import asyncio
import json
from typing import Any


class SseHub:
    """进程内 SSE 广播总线，用于向前端推送新邮件事件。"""

    def __init__(self) -> None:
        """初始化进程内 SSE 客户端集合。"""

        self.clients: dict[asyncio.Queue[str | None], asyncio.AbstractEventLoop] = {}

    def add(self) -> asyncio.Queue[str | None]:
        """注册一个 SSE 客户端并返回其消息队列。"""

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.clients[queue] = asyncio.get_running_loop()
        queue.put_nowait(": connected\n\n")
        return queue

    def remove(self, queue: asyncio.Queue[str | None]) -> None:
        """移除 SSE 客户端并通知生成器结束。"""

        self.clients.pop(queue, None)
        queue.put_nowait(None)

    def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        """向所有 SSE 客户端广播事件。"""

        message = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        for client, loop in list(self.clients.items()):
            try:
                loop.call_soon_threadsafe(client.put_nowait, message)
            except RuntimeError:
                self.clients.pop(client, None)


sse_hub = SseHub()
