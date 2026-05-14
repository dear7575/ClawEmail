from dataclasses import dataclass


@dataclass
class ListenerState:
    """单个邮箱监听器的运行状态快照。"""

    connection_id: str
    email: str
    status: str = "stopped"
    connected: bool = False
    retry: int = 0
    error: str | None = None


class ListenerManager:
    """监听器状态管理器；WebSocket 实现会在迁移后挂到这里。"""

    def __init__(self) -> None:
        """初始化进程内监听器状态容器。"""

        self.listeners: dict[str, ListenerState] = {}

    @staticmethod
    def key(connection_id: str, email: str) -> str:
        """生成监听器状态字典键。"""

        return f"{connection_id}:{email.strip().lower()}"

    def snapshot(self) -> list[dict]:
        """返回前端可展示的监听器状态列表。"""

        return [
            {
                "connectionId": item.connection_id,
                "email": item.email,
                "status": item.status,
                "connected": item.connected,
                "retry": item.retry,
                "error": item.error,
            }
            for item in self.listeners.values()
        ]

    def mark_stopped(self, email: str, connection_id: str = "legacy") -> None:
        """标记指定邮箱监听器已停止。"""

        self.listeners.pop(self.key(connection_id, email), None)


listener_manager = ListenerManager()
