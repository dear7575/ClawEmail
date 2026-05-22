import inspect

from app.main import app
from app.api.health import health


def test_health_handler_is_async() -> None:
    """健康检查必须避开同步线程池，避免长同步任务导致容器误判失败。"""

    assert inspect.iscoroutinefunction(health)


def test_health_returns_python_runtime(test_client) -> None:
    client = test_client
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "runtime": "python"
    }
