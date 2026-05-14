import os

import pytest
from fastapi.testclient import TestClient


os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


class AuthenticatedTestClient(TestClient):
    def request(self, method, url, **kwargs):
        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault("x-admin-password", "admin@123456")
        return super().request(method, url, headers=headers, **kwargs)


@pytest.fixture
def test_client():
    from app.main import app

    return AuthenticatedTestClient(app)
