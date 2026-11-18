"""Jackson Hole Mountain Resort: snow totals, current weather and webcams.

From the JSON feeds behind jacksonhole.com's mountain report: snow.json (summit and
mid-mountain snow, current temperatures and wind, and the daily report text) and
web-cams.json (still images, some of them panoramic cameras).
"""

import asyncio
import re
from datetime import datetime

import httpx

from .models import DASH, Resort, Stat, Table, Webcam, degrees_f, inches, mph

ID = "jackson-hole"
NAME = "Jackson Hole"
WEBSITE = "https://www.jacksonhole.com"
API_URL = "https://jacksonhole-prod.zaneray.com/api"
FEATURED_WEBCAM = "Cody Bowl"

# Their fields -> our labels, in display order.
SNOW_FIELDS = {
    "newSnowSince6am": "Since 6 a.m.",
    "newSnowSinceLiftsClosed": "Overnight",
    "newSnowLast24H": "Last 24 hours",
    "newSnowLast48H": "Last 48 hours",
    "newSnowLast7D": "Last 7 days",
    "totalSnowDepth": "Snow depth",
    "seasonTotalSnow": "Season total",
}
LOCATIONS = {"tramSummit": "Summit", "midMountain": "Mid-mountain", "base": "Base"}


def value(field: dict | None) -> str | None:
    """{"unit": "INCH", "value": "6"} -> "6"; {} -> None."""
    return (field or {}).get("value")


def clean_name(name: str) -> str:
    """'Tram Summit (Current)' -> 'Tram Summit'."""
    return re.sub(r"\s*\(.*?\)", "", name).strip()


def build(snow: dict, cams: dict) -> Resort:
    report = snow["snow"]
    locations = [(label, report[key]) for key, label in LOCATIONS.items() if report.get(key)]
    summit = report.get("tramSummit") or {}

    summary = [
        Stat(label, inches(value(summit.get(field))), "Summit")
        for field, label in (("newSnowLast24H", "Last 24 hours"), ("totalSnowDepth", "Snow depth"), ("seasonTotalSnow", "Season total"))
        if value(summit.get(field)) is not None
    ]
    snow_rows = [
        [label, *(inches(value(spot.get(field))) for field in SNOW_FIELDS)]
        for label, spot in locations
    ]
    weather_rows = [
        [clean_name(spot.get("name", key)), degrees_f(value(spot.get("temperature"))), mph(value(spot.get("wind")))]
        for key, spot in snow.get("weather", {}).items()
        if spot
    ]
    webcams = [
        Webcam(c.get("caption") or c.get("altText") or "Webcam", "image", c["thumbUrl"] if c.get("isPanoCam") else c["url"], c["url"])
        for c in cams.get("webcams", [])
        if c.get("url") and not c.get("liveStreamUrl") and not c.get("youtubeId")
    ]
    names = [w.name for w in webcams]
    return Resort(
        id=ID, name=NAME, website=WEBSITE, source="Jackson Hole Mountain Resort", source_url=f"{WEBSITE}/mountain-report",
        updated=datetime.fromisoformat(snow["lastModified"]).isoformat() if snow.get("lastModified") else None,
        summary=summary,
        webcams=webcams,
        featured_webcam=names.index(FEATURED_WEBCAM) if FEATURED_WEBCAM in names else (0 if webcams else None),
        tables=[
            Table("Snow", ["Location", *SNOW_FIELDS.values()], snow_rows),
            Table("Current weather", ["Location", "Temperature", "Wind"], weather_rows or [[DASH, DASH, DASH]]),
        ],
        message=(report.get("detail") or "").strip() or None,
    )


async def fetch(client: httpx.AsyncClient) -> Resort:
    snow, cams = await asyncio.gather(client.get(f"{API_URL}/snow.json"), client.get(f"{API_URL}/web-cams.json"))
    snow.raise_for_status()
    return build(snow.json(), cams.json() if cams.is_success else {})
