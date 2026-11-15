from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from devops_server.main import create_app


def test_liveness_and_api_contract_do_not_require_a_database(auth_settings):
    with TestClient(create_app(auth_settings)) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "devops-server"}
        assert "/api/health/ready" in client.get("/api/openapi.json").json()["paths"]


def test_readiness_returns_503_without_exposing_connection_details(monkeypatch, auth_settings):

    def unavailable():
        raise OperationalError("SELECT 1", {}, Exception("private connection details"))

    with TestClient(create_app(auth_settings)) as client:
        monkeypatch.setattr(client.app.state.engine, "connect", unavailable)
        response = client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable", "database": "unavailable"}


def test_readiness_connects_to_local_postgres(system):
    response = system.client().get("/api/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "connected", "redis": "connected"}
