import importlib

from fastapi.testclient import TestClient

from app.main import app


def reset_network_service(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'auth.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    import app.core.config as config_module
    import app.db.settings_repository as repository_module
    import app.services.network_settings as service_module
    import app.api.system as system_module

    config_module.get_settings.cache_clear()
    importlib.reload(repository_module)
    importlib.reload(service_module)
    system_module.network_settings_service = service_module.network_settings_service


def test_api_requires_admin_password() -> None:
    client = TestClient(app)

    response = client.get("/api/system/network-settings")

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_api_accepts_query_token(tmp_path, monkeypatch) -> None:
    reset_network_service(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/api/system/network-settings?token=admin%40123456")

    assert response.status_code == 200


def test_health_does_not_require_admin_password() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
