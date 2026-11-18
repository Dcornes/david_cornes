"""Big Sky Resort: snow stake readings and live webcams.

Snow totals come from Big Sky's snow depth page (the one its website embeds), which
shows each station's new snow since 4 p.m., last 24 hours, 2, 3 and 7 days, and base
depth. Webcams are the CamStreamer live players on its webcams page, which are made to
be embedded. Neither page has a timestamp, so the fetch time is used.
"""

import asyncio
import html
import re
from datetime import datetime, timezone

import httpx

from .models import DASH, Resort, Stat, Table, Webcam, inch_range, inches

ID = "big-sky"
NAME = "Big Sky Resort"
WEBSITE = "https://www.bigskyresort.com"
SNOW_URL = "https://app-int-eus-bsr-snowdepth.azurewebsites.net/"
WEBCAMS_URL = "https://www.bigskyresort.com/current-conditions/webcams"
FEATURED_WEBCAM = "Lone Peak Tram"

# Big Sky's labels -> ours, in the order the page shows them.
PERIODS = {
    "New Snow 4 p.m. to Now": "Since 4 p.m.",
    "Last 24 Hours": "Last 24 hours",
    "Last 2 Days": "Last 2 days",
    "Last 3 Days": "Last 3 days",
    "Last 7 Days": "Last 7 days",
    "Base Depth": "Base depth",
}

PANEL = re.compile(r'<section[^>]*role="tabpanel"[^>]*id="panel_([^"]+)"(.*?)</section>', re.S)
TAB = re.compile(r'id="tab_([^"]+)"[^>]*>\s*<span[^>]*>([^<]+)</span>')
FIGURE = re.compile(r'<div class="text-4xl[^"]*">(.*?)</div>\s*<div class="text-sm[^"]*">(.*?)</div>', re.S)
IFRAME = re.compile(r"<iframe\b[^>]*>")
ATTR = re.compile(r'(\w+)="([^"]*)"')


def text(fragment: str) -> str:
    """'6<span>&quot;</span>' -> '6"'; 'New Snow<br />4 p.m. to Now' -> 'New Snow 4 p.m. to Now'."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip().replace(' "', '"')


def parse_snow(page: str) -> list[dict]:
    """[{"name": "Andesite", "offline": False, "Last 24 hours": '6"', ...}, ...] from the snow depth page."""
    names = {key: html.unescape(name).strip() for key, name in TAB.findall(page)}
    stations = []
    for key, panel in PANEL.findall(page):
        figures = {text(label): text(value) for value, label in FIGURE.findall(panel)}
        station = {"name": names.get(key, key), "offline": "station is offline" in panel}
        for theirs, ours in PERIODS.items():
            station[ours] = figures.get(theirs)
        stations.append(station)
    return stations


def parse_webcams(page: str) -> list[Webcam]:
    """The CamStreamer players on the webcams page, by their iframe titles."""
    cams, seen = [], set()
    for tag in IFRAME.findall(page):
        attrs = {k: html.unescape(v) for k, v in ATTR.findall(tag)}
        src = attrs.get("src", "")
        if "camstreamer.com/embed/" not in src or src in seen:
            continue
        seen.add(src)
        name = attrs.get("title", "Webcam").removesuffix(" Camera").strip()
        cams.append(Webcam(name, "embed", src, WEBCAMS_URL))
    return cams


def build(stations: list[dict], webcams: list[Webcam], now: datetime) -> Resort:
    live = [s for s in stations if not s["offline"]]

    def across(period: str) -> str:
        return inch_range(s[period] for s in live)

    summary = [Stat(label, across(label)) for label in ("Last 24 hours", "Last 7 days", "Base depth")]
    rows = [
        [s["name"]] + ([inches(s[p]) for p in PERIODS.values()] if not s["offline"] else ["Offline"] + [DASH] * (len(PERIODS) - 1))
        for s in stations
    ]
    names = [w.name for w in webcams]
    return Resort(
        id=ID, name=NAME, website=WEBSITE, source="Big Sky Resort", source_url=WEBSITE,
        updated=now.isoformat(),
        summary=summary,
        webcams=webcams,
        featured_webcam=names.index(FEATURED_WEBCAM) if FEATURED_WEBCAM in names else (0 if webcams else None),
        tables=[Table(
            "Snow stations", ["Station", *PERIODS.values()], rows,
            "Ranges in the summary span the stations that are online.",
        )],
    )


async def fetch(client: httpx.AsyncClient) -> Resort:
    snow, cams = await asyncio.gather(client.get(SNOW_URL), client.get(WEBCAMS_URL, follow_redirects=True))
    snow.raise_for_status()
    stations = parse_snow(snow.text)
    if not stations:
        raise RuntimeError("No stations found on Big Sky's snow depth page (has it changed?)")
    webcams = parse_webcams(cams.text) if cams.is_success else []
    return build(stations, webcams, datetime.now(timezone.utc))
