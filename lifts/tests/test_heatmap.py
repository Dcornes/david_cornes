from datetime import date, datetime, timedelta

from lift_vertical import heatmap
from lift_vertical.day_store import SkiDay
from lift_vertical.laps import Ride


def depths(start: datetime, values: list[float]) -> dict[datetime, float]:
    return {start + timedelta(hours=i): v for i, v in enumerate(values)}


def test_new_snow_is_the_storm_rise_over_48_hours_ignoring_glitches_and_settling() -> None:
    # 49 hours to 6 am PST: 40" steady, a 6" storm, settles 2", another 6" overnight, a glitch.
    values = [40.0] * 10 + [42, 44, 46] + [46.0] * 10 + [45, 44] + [44.0] * 10 + [46, 48, 50] + [50.0] * 11
    values[20] = 20.0  # a glitch, smoothed away
    readings = depths(datetime(2024, 12, 31, 5), values)
    assert len(values) == 49
    assert heatmap.new_snow(readings, date(2025, 1, 2)) == 10.0  # 40 -> 50, settling included


def test_new_snow_is_unknown_without_enough_readings() -> None:
    assert heatmap.new_snow(depths(datetime(2025, 1, 1, 1), [40.0] * 5), date(2025, 1, 2)) is None


def test_heatmap_counts_runs_only_on_deep_days() -> None:
    ride = [Ride("Ramcharger 8", 0.0, 500.0, 300.0, 2.0)]
    days = [
        SkiDay("Big Sky", ride, [], "2025-01-02", {"1": 3, "2": 1}, [[0, 0, 2], [0, 1, 1]]),
        SkiDay("Big Sky", ride, [], "2025-01-05", {"1": 1}, [[5, 5, 9]]),
        SkiDay("Big Sky", ride, [], "2025-01-09", {"3": 4}),  # no snow data
        SkiDay("Bridger Bowl", ride, [], "2025-01-02", {"9": 9}),
    ]
    snow = heatmap.SnowHistory()
    snow.snow = {("Big Sky", "2025-01-02"): 12.0, ("Big Sky", "2025-01-05"): 2.0, ("Big Sky", "2025-01-09"): None}
    result = heatmap.heatmap(days, "Big Sky", 8, snow)
    assert result["counts"] == {"1": 3, "2": 1}
    assert (result["days"], result["days_with_runs"], result["days_unknown_snow"]) == (1, 3, 1)
    assert result["snow_amounts"] == [12.0, 2.0]
    assert sorted(result["heat"]["cells"]) == [[0, 0, 2], [0, 1, 1]] and result["heat"]["cell_m"] == 10
    assert "2025" not in str(result)  # no dates published


def test_heat_cells_draw_between_points_and_count_each_pass() -> None:
    from lift_vertical.runs import heat_cells
    from lift_vertical.strava import Track

    lat0, lon0 = 45.0, -111.0
    north = lambda m: lat0 + m / 110_540
    # Up 100 m in two big GPS jumps, then back down: every cell on the way is passed twice.
    track = Track(time=[0, 10, 20, 30, 40], lat=[north(0), north(50), north(100), north(50), north(0)],
                  lon=[lon0] * 5, altitude=[0] * 5, speed=None)
    cells = {(x, y): n for x, y, n in heat_cells(track, (lat0, lon0), 5000)}
    assert set(cells) == {(0, y) for y in range(0, 11)}
    assert cells[(0, 5)] == 2 and cells[(0, 10)] == 1  # the top is reached once


def test_snow_history_uses_saved_days_and_saves_new_ones(monkeypatch) -> None:
    import asyncio

    class FakeStore:
        def __init__(self):
            self.saved = {}

        def load_snow(self, window_hours):
            return {("Big Sky", "2025-01-02"): 11.0}

        def save_snow(self, window_hours, snow):
            self.saved.update(snow)

    fetches = []

    async def fake_fetch(client, triplet, begin, end):
        fetches.append((begin, end))
        return {}  # no readings: every station is tried

    monkeypatch.setattr(heatmap, "fetch_depths", fake_fetch)
    store = FakeStore()
    snow = heatmap.SnowHistory(store)
    asyncio.run(snow.ensure(None, "Big Sky", ["2025-01-02"]))
    assert fetches == [] and snow.get("Big Sky", "2025-01-02") == 11.0  # from the database, no fetch
    asyncio.run(snow.ensure(None, "Big Sky", ["2025-01-02", "2025-01-09"]))
    assert len(fetches) == 1 and store.saved[("Big Sky", "2025-01-09")] == (None, "Lone Mountain SNOTEL (590:MT:SNTL)")

    async def fake_reports(client):
        return {"2024-01-10": 9.0}

    monkeypatch.setattr(heatmap, "fetch_bridger_reports", fake_reports)
    asyncio.run(snow.ensure(None, "Bridger Bowl", ["2024-01-09", "2024-01-10"]))
    assert store.saved[("Bridger Bowl", "2024-01-10")] == (9.0, heatmap.BRIDGER_SOURCE)  # the resort's report
    assert len(fetches) == 3 and "Sacajawea" in store.saved[("Bridger Bowl", "2024-01-09")][1]  # no report: SNOTEL


def test_morning_reports_take_the_first_report_before_10am() -> None:
    reports = [
        {"date": "2024-03-03 11:54:25", "recent_snow": 30},
        {"date": "2024-03-03 05:57:59", "recent_snow": 24},
        {"date": "2024-03-03 06:50:12", "recent_snow": 25},
        {"date": "2024-03-04 14:00:00", "recent_snow": 3},  # afternoon only: no morning report
    ]
    assert heatmap.morning_reports(reports) == {"2024-03-03": 24.0}
