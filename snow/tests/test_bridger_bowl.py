import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from snow_report import bridger_bowl
from snow_report.bridger_bowl import fetch_stations, fetch_webcams

# Trimmed real responses from Bridger Bowl's GraphQL API.
STATIONS = [
    {"name": "base", "display_name": "Base Area", "sensors": '["temperature","wind","gusts","wind_direction"]', "enabled": "1"},
    {"name": "apron", "display_name": "Apron", "sensors": '["temperature","total_snow","new_snow"]', "enabled": "1"},
    {"name": "alpine", "display_name": "Alpine", "sensors": '["temperature","total_snow","new_snow"]', "enabled": "1"},
    {"name": "pumphouse", "display_name": "Pumphouse", "sensors": '["water_temp","water_depth"]', "enabled": "1"},
    {"name": "old", "display_name": "Old", "sensors": '["temperature"]', "enabled": "0"},
]
READINGS = {
    "base": {"date": "2026-11-18 11:00:00", "temperature": 28, "wind": 13, "gusts": 42, "wind_direction": 260, "new_snow": None, "total_snow": None},
    "apron": {"date": "2026-11-18 11:00:00", "temperature": 21, "wind": 5, "gusts": 11, "wind_direction": 108, "new_snow": 11.7, "total_snow": 11.7},
    "alpine": {"date": "2026-11-17 11:50:00", "temperature": 42, "wind": 2, "gusts": 3, "wind_direction": 305, "new_snow": 0, "total_snow": 0},
    "pumphouse": {"date": "2026-11-18 11:00:00", "temperature": None, "wind": None, "gusts": None, "wind_direction": None, "new_snow": None, "total_snow": None},
}
REPORT = {
    "date": "2026-03-22 16:11:43", "new_snow": 0, "recent_snow": 2, "seasonal_snow": 138, "settled_snow": 43,
    "conditions_primary": "Springtime", "conditions_secondary": "Groomed", "message": "That's a wrap!",
}
WEBCAMS = [
    {"name": "Alpine Apron", "area": "Mid Mountain", "url": "https://webcam-images.s3-us-west-1.amazonaws.com/apron.jpg?r=dElOU7YnpX", "enabled": True},
    {"name": "Ridge North", "area": "Ridge", "url": "https://webcam-images.s3-us-west-1.amazonaws.com/ridge_north.jpg?r=a?r=b", "enabled": True},
    {"name": "Retired", "area": "Ridge", "url": "https://webcam-images.s3-us-west-1.amazonaws.com/old.jpg", "enabled": False},
]
NOW = datetime(2026, 11, 18, 19, 0, tzinfo=timezone.utc)  # noon in Bozeman


def handler(request: httpx.Request) -> httpx.Response:
    if request.method == "HEAD":  # webcam image
        if request.url.path == "/ridge_north.jpg":
            return httpx.Response(403)
        return httpx.Response(200, headers={"Last-Modified": "Wed, 18 Nov 2026 18:11:18 GMT"})
    query = json.loads(request.content)["query"]
    if "webcams" in query:
        return httpx.Response(200, json={"data": {"webcams": WEBCAMS}})
    if "weather_stations" in query:
        return httpx.Response(200, json={"data": {"weather_stations": STATIONS}})
    if "snow_reports" in query:
        return httpx.Response(200, json={"data": {"snow_reports": {"data": [REPORT]}}})
    station = query.split('station: "')[1].split('"')[0]
    return httpx.Response(200, json={"data": {"weather_readings": {"data": [READINGS[station]]}}})


def run(fetcher, transport_handler=handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport_handler)) as client:
            return await fetcher(client)

    return asyncio.run(go())


def test_stations_put_snow_stations_first_and_skip_ones_with_nothing_to_show() -> None:
    stations = run(fetch_stations)
    # Pumphouse only measures water; "old" is disabled.
    assert [s["name"] for s in stations] == ["Apron", "Alpine", "Base Area"]
    apron = stations[0]
    assert (apron["new_snow"], apron["wind"], apron["time"].isoformat()) == (11.7, None, "2026-11-18T11:00:00-07:00")


def test_webcams_have_clean_urls_and_update_times() -> None:
    cams = run(fetch_webcams)
    assert [(c.name, c.src, c.updated) for c in cams] == [
        ("Alpine Apron", "https://webcam-images.s3-us-west-1.amazonaws.com/apron.jpg", "2026-11-18T18:11:18+00:00"),
        ("Ridge North", "https://webcam-images.s3-us-west-1.amazonaws.com/ridge_north.jpg", None),  # HEAD failed
    ]


def test_build_summarises_recent_stations_and_labels_an_old_report() -> None:
    stations, cams = run(fetch_stations), run(fetch_webcams)
    resort = bridger_bowl.build(stations, REPORT, cams, NOW)

    # Alpine last reported yesterday, so only Apron counts; last season's report gives no season total.
    assert [(s.label, s.value, s.detail) for s in resort.summary] == [
        ("New snow", "11.7″", "Apron"), ("Total snow", "11.7″", "Apron"),
    ]
    assert resort.featured_webcam == 0 and resort.message is None
    snow, report, weather = resort.tables
    assert snow.rows[1] == ["Alpine", "0″", "0″", "42°F", "Nov 17, 11:50 AM MST (out of date)"]
    assert report.rows == [["138″", "2″", "0″", "43″", "Springtime · Groomed"]]
    assert "once the ski area opens" in report.note
    assert weather.rows[2] == ["Base Area", "28°F", "13 mph", "42 mph", "W (260°)", "Nov 18, 11:00 AM MST"]


def test_current_report_adds_season_total_and_message() -> None:
    report = {**REPORT, "date": "2026-11-18 07:00:00"}
    resort = bridger_bowl.build(run(fetch_stations), report, [], NOW)
    assert resort.summary[-1].label == "Season total" and resort.message == "That's a wrap!"
    assert resort.featured_webcam is None


def test_api_errors_are_raised() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Syntax Error"}]})

    with pytest.raises(RuntimeError, match="Syntax Error"):
        run(fetch_stations, failing)
