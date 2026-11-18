"""Bridger Bowl: weather stations, the ski patrol's snow report, and webcams.

All from Bridger Bowl's public GraphQL API (the one its website uses):
- weather stations: the latest reading from each (new and total snow where the
  station measures them, plus temperature and wind);
- the snow report: new snow, 48-hour and season totals. Posted daily while the area
  is open; out of season the newest one is last season's, so it's labelled as such;
- webcams: JPEGs on Bridger Bowl's S3 bucket, replaced every few minutes. The update
  time of each comes from a HEAD request.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx

from .models import DASH, Resort, Stat, Table, Webcam, degrees_f, direction, inches, local_label, mph

ID = "bridger-bowl"
NAME = "Bridger Bowl"
WEBSITE = "https://bridgerbowl.com"
API_URL = "https://api.bridgerbowl.com/graphql"
ZONE = ZoneInfo("America/Denver")  # the API's dates are Bozeman local time, with no zone
FEATURED_WEBCAM = "Alpine Apron"
STALE = timedelta(days=1)  # older station readings are marked out of date
SUMMARY_AGE = timedelta(hours=6)  # only stations reporting this recently count toward the summary
CURRENT_REPORT = timedelta(days=7)  # an older snow report is from a previous season

STATIONS_QUERY = "{ weather_stations { name display_name sensors enabled } }"
READING_FIELDS = "date temperature wind gusts wind_direction new_snow total_snow"
REPORT_QUERY = """{ snow_reports(first: 1) { data {
  date new_snow recent_snow seasonal_snow settled_snow conditions_primary conditions_secondary message
} } }"""
WEBCAMS_QUERY = "{ webcams { name area url enabled } }"
SNOW_SENSORS = {"new_snow", "total_snow"}
SHOWN_FIELDS = ("new_snow", "total_snow", "temperature", "wind", "gusts", "wind_direction")


def local_time(value: str | None) -> datetime | None:
    """'2026-11-18 11:00:00' (Bozeman time) -> an aware datetime."""
    return datetime.fromisoformat(value).replace(tzinfo=ZONE) if value else None


async def graphql(client: httpx.AsyncClient, query: str) -> dict:
    response = await client.post(API_URL, json={"query": query})
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(f"Bridger Bowl API error: {body['errors'][0].get('message')}")
    return body["data"]


def station_row(station: dict, reading: dict | None) -> dict:
    """One station's latest reading, keeping only what its sensors measure."""
    sensors = set(json.loads(station.get("sensors") or "[]"))
    reading = reading or {}
    row = {field: reading.get(field) if field in sensors else None for field in SHOWN_FIELDS}
    return {
        "name": station["display_name"] or station["name"],
        "measures_snow": bool(sensors & SNOW_SENSORS),
        "time": local_time(reading.get("date")),
        **row,
    }


async def fetch_stations(client: httpx.AsyncClient) -> list[dict]:
    """Latest reading from every enabled station with something to show, snow stations first."""
    stations = [s for s in (await graphql(client, STATIONS_QUERY))["weather_stations"] if s.get("enabled") == "1"]

    async def latest(name: str) -> dict | None:
        data = await graphql(client, f'{{ weather_readings(station: "{name}", first: 1) {{ data {{ {READING_FIELDS} }} }} }}')
        readings = data["weather_readings"]["data"]
        return readings[0] if readings else None

    readings = await asyncio.gather(*(latest(s["name"]) for s in stations))
    rows = [station_row(s, r) for s, r in zip(stations, readings)]
    # Leave out stations with nothing to show (never reported, or only water sensors like Pumphouse).
    rows = [r for r in rows if r["time"] and any(r[f] is not None for f in SHOWN_FIELDS)]
    return sorted(rows, key=lambda r: not r["measures_snow"])  # otherwise in Bridger's order


async def fetch_webcams(client: httpx.AsyncClient) -> list[Webcam]:
    """Enabled webcams. The API adds random ?r= values to the URLs; those are dropped
    (the page adds its own cache-buster). updated is None if the HEAD request fails."""
    cams = [c for c in (await graphql(client, WEBCAMS_QUERY))["webcams"] if c.get("enabled")]
    images = [c["url"].split("?", 1)[0] for c in cams]

    async def updated(url: str) -> str | None:
        try:
            response = await client.head(url)
            response.raise_for_status()
            return parsedate_to_datetime(response.headers["last-modified"]).astimezone(timezone.utc).isoformat()
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return None

    times = await asyncio.gather(*(updated(url) for url in images))
    return [Webcam(c["name"], "image", url, url, time) for c, url, time in zip(cams, images, times)]


def build(stations: list[dict], report: dict | None, webcams: list[Webcam], now: datetime) -> Resort:
    fresh = [s for s in stations if s["measures_snow"] and now - s["time"] <= SUMMARY_AGE]
    report_time = local_time(report["date"]) if report else None
    current_report = report if report and now - report_time <= CURRENT_REPORT else None

    # The highest new and total snow among stations reporting now, and where.
    summary = []
    for field, label in (("new_snow", "New snow"), ("total_snow", "Total snow")):
        measured = [s for s in fresh if s[field] is not None]
        if measured:
            top = max(measured, key=lambda s: s[field])
            summary.append(Stat(label, inches(top[field]), top["name"]))
    if current_report:
        summary.append(Stat("Season total", inches(current_report["seasonal_snow"])))

    def reading(s: dict) -> str:
        label = local_label(s["time"], ZONE)
        return f"{label} (out of date)" if now - s["time"] > STALE else label

    tables = [
        Table(
            "Snow stations",
            ["Station", "New snow", "Total snow", "Temperature", "Reading"],
            [[s["name"], inches(s["new_snow"]), inches(s["total_snow"]), degrees_f(s["temperature"]), reading(s)]
             for s in stations if s["measures_snow"]],
        ),
    ]
    if report:
        conditions = " · ".join(c for c in (report["conditions_primary"], report["conditions_secondary"]) if c)
        tables.append(Table(
            "Ski patrol snow report",
            ["Season total", "Last 48 hours", "New snow", "Snow depth", "Conditions"],
            [[inches(report["seasonal_snow"]), inches(report["recent_snow"]), inches(report["new_snow"]),
              inches(report["settled_snow"]), conditions or DASH]],
            f"Posted {local_label(report_time, ZONE)}."
            + ("" if current_report else " Reports are posted daily once the ski area opens for the season."),
        ))
    tables.append(Table(
        "Weather stations",
        ["Station", "Temperature", "Wind", "Gusts", "Direction", "Reading"],
        [[s["name"], degrees_f(s["temperature"]), mph(s["wind"]), mph(s["gusts"]), direction(s["wind_direction"]), reading(s)]
         for s in stations],
    ))

    names = [w.name for w in webcams]
    times = [s["time"] for s in fresh] or [s["time"] for s in stations]
    return Resort(
        id=ID, name=NAME, website=WEBSITE, source="Bridger Bowl", source_url=WEBSITE,
        updated=max(times).isoformat() if times else None,
        summary=summary,
        webcams=webcams,
        featured_webcam=names.index(FEATURED_WEBCAM) if FEATURED_WEBCAM in names else (0 if webcams else None),
        tables=tables,
        message=current_report["message"] if current_report else None,
    )


async def fetch(client: httpx.AsyncClient) -> Resort:
    stations, report, webcams = await asyncio.gather(
        fetch_stations(client), graphql(client, REPORT_QUERY), fetch_webcams(client)
    )
    newest_report = next(iter(report["snow_reports"]["data"]), None)
    return build(stations, newest_report, webcams, datetime.now(timezone.utc))
