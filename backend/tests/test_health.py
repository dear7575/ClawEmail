from app.main import app


def test_health_returns_python_runtime(test_client) -> None:
    client = test_client
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "runtime": "python"
    }
