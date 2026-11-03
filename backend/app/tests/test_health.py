from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_never_raises_when_dependencies_are_down() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["app"] == "ok"
    assert body["db"] in {"ok", "error"}
    assert body["redis"] in {"ok", "error"}
    assert body["version"] == "0.1.0"
    assert set(body) == {"app", "db", "redis", "version"}
