from fastapi.testclient import TestClient

from app import lifts
from app.main import app


def test_endpoint_returns_latest_or_503(monkeypatch) -> None:
    client = TestClient(app)  # not started as a context manager, so no background fetching
    monkeypatch.setattr(lifts.cache, "latest", None)
    assert client.get("/api/lifts").status_code == 503

    data = {"fetched_at": "t", "source": "s", "resorts": [], "failed": [], "lifts": []}
    monkeypatch.setattr(lifts.cache, "latest", data)
    assert client.get("/api/lifts").json() == data
