from fastapi.testclient import TestClient

from app import snow
from app.main import app


def test_endpoint_returns_cached_report_or_503(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(snow.report, "enabled", False)  # no real fetching in tests
    monkeypatch.setattr(snow.report, "_loaded", True)
    monkeypatch.setattr(snow.report, "data", None)
    assert client.get("/api/snow").status_code == 503

    data = {"fetched_at": "t", "resorts": [{"id": "bridger-bowl", "name": "Bridger Bowl", "summary": []}]}
    monkeypatch.setattr(snow.report, "data", data)
    response = client.get("/api/snow")
    assert response.json() == data and "max-age" in response.headers["cache-control"]


def test_stale_report_is_refreshed_once_in_the_background(monkeypatch) -> None:
    import asyncio
    import time

    calls = []

    async def fetch(previous):
        calls.append(previous)
        await asyncio.sleep(0.01)
        return {"fetched_at": "new", "resorts": []}

    value = snow.StoredValue("test", 60, fetch)
    monkeypatch.setattr(value, "store", lambda: None)

    async def scenario():
        value.data, value.saved_at = {"fetched_at": "old", "resorts": []}, time.time() - 120  # stale
        first = await value.get()  # returns the old data at once and refreshes behind
        second = await value.get()  # a refresh is already running: no second one
        await asyncio.sleep(0.05)
        return first, second, await value.get()

    first, second, third = asyncio.run(scenario())
    assert first["fetched_at"] == "old" and second["fetched_at"] == "old" and third["fetched_at"] == "new"
    assert len(calls) == 1
