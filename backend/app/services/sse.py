import json
from queue import Queue
from typing import Any


class SseHub:
    """进程内 SSE 广播总线，用于向前端推送新邮件事件。"""

    def __init__(self) -> None:
        """初始化进程内 SSE 客户端集合。"""

        self.clients: set[Queue[str | None]] = set()

    def add(self) -> Queue[str | None]:
        """注册一个 SSE 客户端并返回其消息队列。"""

        queue: Queue[str | None] = Queue()
        self.clients.add(queue)
        queue.put(": connected\n\n")
        return queue

    def remove(self, queue: Queue[str | None]) -> None:
        """移除 SSE 客户端并通知生成器结束。"""

        self.clients.discard(queue)
        queue.put(None)

    def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        """向所有 SSE 客户端广播事件。"""

        message = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        for client in list(self.clients):
            client.put(message)


sse_hub = SseHub()
