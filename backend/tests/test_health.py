import inspect

from app.main import app
from app.api.health import health, sync_health
from app.api.events import stream_events


def test_health_handler_is_async() -> None:
    """健康检查必须避开同步线程池，避免长同步任务导致容器误判失败。"""

    assert inspect.iscoroutinefunction(health)


def test_sse_handler_is_async() -> None:
    """SSE 长连接必须避开同步线程池，避免耗尽业务接口执行线程。"""

    assert inspect.iscoroutinefunction(stream_events)


def test_sync_health_handler_is_not_async() -> None:
    """同步健康检查必须走业务线程池，用于发现线程池耗尽。"""

    assert not inspect.iscoroutinefunction(sync_health)


def test_health_returns_python_runtime(test_client) -> None:
    client = test_client
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["runtime"] == "python"
    assert "revision" in data


def test_sync_health_returns_python_runtime(test_client) -> None:
    client = test_client
    response = client.get("/health/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["runtime"] == "python-sync"
    assert "revision" in data
