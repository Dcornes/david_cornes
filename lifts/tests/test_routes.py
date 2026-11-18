from lift_vertical import routes
from lift_vertical.aerialways import RESORT_CENTERS

LAT, LON = RESORT_CENTERS["Bridger Bowl"]
M = 1 / 110_540  # degrees of latitude per meter


def point(north_m: float, east_m: float = 0.0) -> dict:
    return {"lat": LAT + north_m * M, "lon": LON + east_m / 77_700}


def way(tags: dict, *points: dict) -> dict:
    return {"type": "way", "tags": tags, "geometry": list(points)}


# A 1 km lift running north. Two ways down: a short expert line straight under it (two runs
# that share a point), and a long easy loop out to the east. A conveyor with no runs nearby.
BODY = {"elements": [
    way({"aerialway": "chair_lift", "name": "Bridger"}, point(0), point(1000)),
    way({"piste:type": "downhill", "piste:name": "Upper Chute", "piste:difficulty": "expert"}, point(1010, 20), point(500, 30)),
    way({"piste:type": "downhill", "piste:name": "Lower Chute", "piste:difficulty": "advanced"}, point(500, 30), point(10, 20)),
    way({"piste:type": "downhill", "piste:name": "Long Way", "piste:difficulty": "easy"},
        point(1010, 20), point(1000, 600), point(0, 600), point(10, 20)),
    way({"piste:type": "downhill", "piste:name": "Meadow", "area": "yes"}, point(0), point(5, 5), point(0, 10)),
    way({"aerialway": "magic_carpet", "name": "Carpet"}, point(3000, 3000), point(3050, 3000)),
]}


def test_route_is_the_shortest_connected_way_down_and_takes_the_hardest_difficulty() -> None:
    result = routes.resort_routes("Bridger Bowl", BODY)
    bridger, carpet = result["lifts"]
    assert bridger["name"] == "Bridger" and bridger["key"] == "bridger"
    route = bridger["route"]
    assert route["runs"] == ["Upper Chute", "Lower Chute"]
    assert 950 <= route["length_m"] <= 1100
    assert route["difficulty"] == "Expert"
    assert carpet["route"] is None  # no runs near it


def test_routes_much_shorter_than_the_lift_are_dropped() -> None:
    # A run that only touches the bottom station: the "route" would be a few meters long.
    body = {"elements": [
        way({"aerialway": "chair_lift", "name": "Deer Park"}, point(0), point(60)),
        way({"piste:type": "downhill", "piste:name": "Stub"}, point(0, 10), point(5, 10)),
    ]}
    (lift,) = routes.resort_routes("Bridger Bowl", body)["lifts"]
    assert lift["route"] is None


def test_simplify_keeps_the_ends() -> None:
    points = [[float(i), 0.0] for i in range(500)]
    short = routes.simplify(points, 50)
    assert len(short) == 50 and short[0] == points[0] and short[-1] == points[-1]


def test_cache_starts_from_the_newer_of_the_snapshot_and_saved_maps(tmp_path) -> None:
    import json

    older = {"fetched_at": "2026-11-01T00:00:00+00:00", "resorts": [{"resort": "Old"}], "failed": []}
    newer = {"fetched_at": "2026-11-18T00:00:00+00:00", "resorts": [{"resort": "New"}], "failed": []}
    bundled, saved = tmp_path / "bundled.json", tmp_path / "saved.json"
    bundled.write_text(json.dumps(newer))
    saved.write_text(json.dumps(older))
    assert routes.RouteCache(saved, bundled).latest["resorts"] == [{"resort": "New"}]
    assert routes.RouteCache(tmp_path / "missing.json", bundled).latest == newer
    assert routes.RouteCache(None, None).latest is None


def test_shipped_snapshot_has_every_resort() -> None:
    cache = routes.RouteCache(None)
    assert sorted(r["resort"] for r in cache.latest["resorts"]) == sorted(RESORT_CENTERS)
    assert cache.latest["failed"] == []


def test_new_strava_days_wake_the_refresh_early(monkeypatch) -> None:
    import asyncio
    import time

    fetched = []

    async def fake_fetch(client):
        fetched.append(time.time())
        return {"fetched_at": "now", "resorts": [], "failed": []}

    monkeypatch.setattr(routes, "fetch_routes", fake_fetch)

    async def scenario() -> None:
        cache = routes.RouteCache(None, None)
        cache.latest, cache.loaded_at = {"resorts": [], "failed": []}, time.time()  # fresh maps
        wake = asyncio.Event()
        task = asyncio.create_task(routes.run_route_updates(cache, refresh=3600, wake=wake))
        await asyncio.sleep(0.05)
        assert fetched == []  # fresh, so no refresh yet
        wake.set()  # new Strava ski days
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(scenario())
    assert len(fetched) == 1


def test_a_failed_resort_keeps_its_previous_map() -> None:
    previous = {"resorts": [{"resort": "A"}, {"resort": "B"}], "failed": []}
    merged = routes.merge(previous, {"resorts": [{"resort": "A"}], "failed": ["B"]})
    assert [r["resort"] for r in merged["resorts"]] == ["A", "B"] and merged["failed"] == []
