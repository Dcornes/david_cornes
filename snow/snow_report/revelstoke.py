"""Revelstoke Mountain Resort: snow report and live webcams.

Both pages are plain HTML on revelstokemountainresort.com. The snow report gives new
snow, the last hour, 24 and 48 hours, 7 days, base depth and season total in
centimetres; they're shown in inches with the centimetres alongside. Webcams are the
Ozolio live players on the webcams page. The pages have no timestamp, so the fetch
time is used.
"""

import asyncio
import html
import re
from datetime import datetime, timezone

import httpx

from .models import DASH, Resort, Stat, Table, Webcam, number, trim

ID = "revelstoke"
NAME = "Revelstoke"
WEBSITE = "https://www.revelstokemountainresort.com"
SNOW_URL = f"{WEBSITE}/mountain/conditions/snow-report/"
WEBCAMS_URL = f"{WEBSITE}/mountain/conditions/webcams/"
FEATURED_WEBCAM = "Gnorm the Powder Gnome"
CM_PER_INCH = 2.54

FIGURE = re.compile(
    r'<h2 class="snow-report__title">\s*([^<]+?)\s*</h2>\s*<span class="snow-report__data">\s*'
    r'<span class="value">\s*([^<]*?)\s*</span>\s*<span class="unit">\s*([^<]*?)\s*</span>',
)
IFRAME = re.compile(r"<iframe\b[^>]*>")
ATTR = re.compile(r'([\w-]+)="([^"]*)"')


def parse_snow(page: str) -> dict[str, float | None]:
    """{"New Snow": 1.0, "24 hours": 1.0, ..., "Season Total": 143.0} in centimetres."""
    figures = {}
    for label, value, unit in FIGURE.findall(page):
        cm = number(value)
        if cm is not None and unit.strip().lower() == "in":
            cm *= CM_PER_INCH
        figures.setdefault(html.unescape(label).strip(), cm)
    return figures


def from_cm(cm: float | None) -> tuple[str, str]:
    """(inches, centimetres) for display, e.g. ('56.3″', '143 cm')."""
    if cm is None:
        return DASH, DASH
    return f"{trim(cm / CM_PER_INCH)}″", f"{trim(cm)} cm"


def parse_webcams(page: str) -> list[Webcam]:
    cams, seen = [], set()
    for tag in IFRAME.findall(page):
        attrs = {k: html.unescape(v).strip() for k, v in ATTR.findall(tag)}
        src = attrs.get("src") or attrs.get("data-src") or ""
        if "relay.ozolio.com" not in src or src in seen:
            continue
        seen.add(src)
        title = attrs.get("title") or attrs.get("alt") or "Webcam"
        name = re.sub(r"^Revelstoke Mountain Resort\s*-\s*", "", title)
        name = re.sub(r"\s*\(.*$", "", name)  # "Gnorm the Powder Gnome (27cm | ...)" -> "Gnorm the Powder Gnome"
        name = re.sub(r"\s*Webcam$", "", name).strip()
        cams.append(Webcam(name or "Webcam", "embed", src, WEBCAMS_URL))
    return cams


def build(figures: dict[str, float | None], webcams: list[Webcam], now: datetime) -> Resort:
    def stat(label: str, key: str) -> Stat | None:
        if figures.get(key) is None:
            return None
        inch, cm = from_cm(figures[key])
        return Stat(label, inch, cm)

    summary = [s for s in (stat("Last 24 hours", "24 hours"), stat("Base depth", "Base Depth"), stat("Season total", "Season Total")) if s]
    rows = [[key, *from_cm(cm)] for key, cm in figures.items()]
    names = [w.name for w in webcams]
    return Resort(
        id=ID, name=NAME, website=WEBSITE, source="Revelstoke Mountain Resort", source_url=SNOW_URL,
        updated=now.isoformat(),
        summary=summary,
        webcams=webcams,
        featured_webcam=names.index(FEATURED_WEBCAM) if FEATURED_WEBCAM in names else (0 if webcams else None),
        tables=[Table("Snow report", ["Period", "Inches", "Centimetres"], rows)],
    )


async def fetch(client: httpx.AsyncClient) -> Resort:
    snow, cams = await asyncio.gather(client.get(SNOW_URL, follow_redirects=True), client.get(WEBCAMS_URL, follow_redirects=True))
    snow.raise_for_status()
    figures = parse_snow(snow.text)
    if not figures:
        raise RuntimeError("No snow figures found on Revelstoke's snow report (has the page changed?)")
    return build(figures, parse_webcams(cams.text) if cams.is_success else [], datetime.now(timezone.utc))
