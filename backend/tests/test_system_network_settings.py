import importlib

from app.main import app


def reset_network_service(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'settings.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    import app.core.config as config_module
    import app.db.settings_repository as repository_module
    import app.services.network_settings as service_module

    config_module.get_settings.cache_clear()
    importlib.reload(repository_module)
    importlib.reload(service_module)
    import app.api.system as system_module

    system_module.network_settings_service = service_module.network_settings_service
    return service_module.network_settings_service.repository


def test_get_network_settings_returns_defaults(tmp_path, monkeypatch, test_client) -> None:
    reset_network_service(tmp_path, monkeypatch)
    client = test_client

    response = client.get("/api/system/network-settings")

    assert response.status_code == 200
    assert response.json() == {
        "proxyUrl": "",
        "timeoutMs": 10000,
        "openAiOtpTimeoutMs": 60000
    }


def test_get_network_settings_uses_legacy_duck_fallback(tmp_path, monkeypatch, test_client) -> None:
    repository = reset_network_service(tmp_path, monkeypatch)
    repository.set("duck.proxyUrl", "http://127.0.0.1:7890")
    repository.set("duck.timeoutMs", "2500")
    client = test_client

    response = client.get("/api/system/network-settings")

    assert response.status_code == 200
    assert response.json()["proxyUrl"] == "http://127.0.0.1:7890/"
    assert response.json()["timeoutMs"] == 2500


def test_put_network_settings_persists_compatible_keys(tmp_path, monkeypatch, test_client) -> None:
    repository = reset_network_service(tmp_path, monkeypatch)
    client = test_client

    response = client.put("/api/system/network-settings", json={
        "proxyUrl": "http://127.0.0.1:6174",
        "timeoutMs": 100000,
        "openAiOtpTimeoutMs": 120000
    })

    assert response.status_code == 200
    assert response.json() == {
        "proxyUrl": "http://127.0.0.1:6174/",
        "timeoutMs": 100000,
        "openAiOtpTimeoutMs": 120000
    }
    assert repository.get("system.proxyUrl") == "http://127.0.0.1:6174/"
    assert repository.get("system.timeoutMs") == "100000"
    assert repository.get("openai.otpTimeoutMs") == "120000"


def test_put_network_settings_rejects_unsupported_proxy_scheme(tmp_path, monkeypatch, test_client) -> None:
    reset_network_service(tmp_path, monkeypatch)
    client = test_client

    response = client.put("/api/system/network-settings", json={
        "proxyUrl": "socks5://127.0.0.1:1080"
    })

    assert response.status_code == 422
