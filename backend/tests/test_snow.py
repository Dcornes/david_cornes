from fastapi.testclient import TestClient

from app import snow
from app.main import app


def test_endpoint_returns_latest_or_503(monkeypatch) -> None:
    client = TestClient(app)  # not started as a context manager, so no background fetching
    monkeypatch.setattr(snow.cache, "resorts", {})
    assert client.get("/api/snow").status_code == 503

    resort = {"id": "bridger-bowl", "name": "Bridger Bowl", "summary": []}
    monkeypatch.setattr(snow.cache, "resorts", {"bridger-bowl": resort})
    monkeypatch.setattr(snow.cache, "fetched_at", "t")
    assert client.get("/api/snow").json() == {"fetched_at": "t", "resorts": [resort]}
