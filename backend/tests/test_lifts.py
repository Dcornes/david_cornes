from fastapi.testclient import TestClient

from app import lifts
from app.main import app


def test_endpoint_returns_latest_or_503(monkeypatch) -> None:
    client = TestClient(app)  # not started as a context manager, so no background fetching
    monkeypatch.setattr(lifts.lift_data, "enabled", False)  # no real fetching in tests
    monkeypatch.setattr(lifts.lift_data, "_loaded", True)
    monkeypatch.setattr(lifts.lift_data, "data", None)
    assert client.get("/api/lifts").status_code == 503

    data = {"fetched_at": "t", "source": "s", "resorts": [], "failed": [], "lifts": []}
    monkeypatch.setattr(lifts.lift_data, "data", data)
    assert client.get("/api/lifts").json() == data


def test_heatmap_publishes_counts_without_dates(monkeypatch) -> None:
    from lift_vertical.day_store import SkiDay
    from lift_vertical.laps import Ride

    client = TestClient(app)
    ride = [Ride("Ramcharger 8", 0.0, 500.0, 300.0, 2.0)]
    monkeypatch.setattr(lifts.my_laps, "days", {1: SkiDay("Big Sky", ride, [], "2025-01-02", {"7": 2})})
    snow = lifts.heatmap.SnowHistory()
    snow.snow = {("Big Sky", "2025-01-02"): 11.0}
    monkeypatch.setattr(lifts, "snow_history", snow)
    body = client.get("/api/lifts/heatmap", params={"resort": "Big Sky", "min_snow": 8}).json()
    assert body["counts"] == {"7": 2} and body["days"] == 1 and "2025" not in str(body)
    assert client.get("/api/lifts/heatmap", params={"min_snow": 12}).json()["counts"] == {}
    assert client.get("/api/lifts/heatmap", params={"resort": "Nowhere"}).status_code == 404

    runs = client.get("/api/lifts/heatmap/runs").json()
    assert runs["resort"] == "Big Sky" and len(runs["runs"]) > 100
