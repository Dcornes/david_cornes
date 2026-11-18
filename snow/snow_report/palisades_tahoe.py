"""Palisades Tahoe: snow depth from the SNOTEL station on the mountain, plus live webcams.

Palisades' own mountain report loads from a third-party feed that needs an access key,
so its numbers come from the USDA's public SNOTEL station "Palisades Tahoe"
(784:CA:SNTL, 8,010 ft, formerly "Squaw Valley G.C."); see snotel.py. Webcams are the
Ozolio live players on its webcams page, which are made to be embedded.
"""

import asyncio
import html
import re
from zoneinfo import ZoneInfo

import httpx

from . import snotel
from .models import Resort, Webcam

ID = "palisades-tahoe"
NAME = "Palisades Tahoe"
WEBSITE = "https://www.palisadestahoe.com"
WEBCAMS_URL = "https://www.palisadestahoe.com/mountain-information/webcams"
STATION = "784:CA:SNTL"
ZONE = ZoneInfo("America/Los_Angeles")
FEATURED_WEBCAM = "Siberia"

# An Ozolio player's URL; the camera's name is the next text on the page.
OZOLIO = re.compile(r"(https://relay\.ozolio\.com/pub\.api\?cmd=embed(?:&amp;|&)oid=EMB_\w+)")
CODE_WORDS = re.compile(r"[a-z0-9]+(-[a-z0-9]+)+(,\s*[a-z0-9]+(-[a-z0-9]+)*)*|[{}\[\]].*")


def camera_name(text: str) -> str:
    """'Palisades Siberia Cam' -> 'Siberia'; 'Alpine Race Cam' -> 'Alpine Race'."""
    name = html.unescape(text).strip()
    name = re.sub(r"\s*\bCam(era)?\b\s*$", "", name, flags=re.I)
    return re.sub(r"^Palisades\s+", "", name).strip()


def parse_webcams(page: str) -> list[Webcam]:
    cams, seen = [], set()
    for match in OZOLIO.finditer(page):
        src = html.unescape(match.group(1))
        if src in seen:
            continue
        seen.add(src)
        # The name is the first text after the player (past any closing tags).
        after = re.sub(r"<[^>]+>", "\n", page[match.end(1): match.end(1) + 800])
        lines = [line.strip() for line in after.split("\n")]
        # Skip attribute leftovers and page-builder class names like "rich-text, responsive-table".
        lines = [line for line in lines if line and not line.startswith('"') and not CODE_WORDS.fullmatch(line)][:3]
        text = next((line for line in lines if re.search(r"\bCam(era)?$", line, re.I)), "")
        cams.append(Webcam(camera_name(text) or f"Webcam {len(cams) + 1}", "embed", src, WEBCAMS_URL))
    return cams


def build(data: list[dict], webcams: list[Webcam]) -> Resort:
    return snotel.build(
        data, id=ID, name=NAME, website=WEBSITE, station_name="Palisades Tahoe", station_number=784,
        elevation="8,010 ft", zone=ZONE, webcams=webcams, featured=FEATURED_WEBCAM,
    )


async def fetch(client: httpx.AsyncClient) -> Resort:
    data, page = await asyncio.gather(snotel.fetch_data(client, STATION), client.get(WEBCAMS_URL, follow_redirects=True))
    return build(data, parse_webcams(page.text) if page.is_success else [])
