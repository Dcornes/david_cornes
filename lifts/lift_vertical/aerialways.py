"""Ski lift locations from OpenStreetMap, for matching GPS tracks to lifts.

Uses the public Overpass API: every aerialway (chairlift, gondola, tram, surface lift)
in a bounding box, with its name and the line it follows. Results are cached per area,
since lifts rarely move. Lift Blog names are matched loosely ("Bridger Lift" ~ "Bridger").
"""

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

# The main public Overpass server, then a mirror; each is tried twice.
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
CACHE_SECONDS = 7 * 24 * 60 * 60
NOT_LIFTS = "station|pylon|goods|zip_line|canopy"
RESORT_BOX = 0.08  # degrees either side of a resort center (about 9 km north-south)

# Rough centers of the ski areas Lift Blog covers here, to label each lift's resort.
RESORT_CENTERS = {
    "Big Sky": (45.2836, -111.4014),
    "Bridger Bowl": (45.8174, -110.8966),
    "Grand Targhee": (43.7896, -110.9585),
    "Jackson Hole": (43.5875, -110.8279),
    "Revelstoke": (50.9583, -118.1636),
}


@dataclass(frozen=True)
class Aerialway:
    name: str
    kind: str  # OSM aerialway value, e.g. "chair_lift", "gondola"
    lat: tuple[float, ...]
    lon: tuple[float, ...]


def resort_near(lat: float, lon: float, max_km: float = 15) -> str | None:
    """The Lift Blog resort within max_km of a point, if any."""
    best, best_km = None, max_km
    for name, (rlat, rlon) in RESORT_CENTERS.items():
        km = (((lat - rlat) * 111) ** 2 + ((lon - rlon) * 111 * 0.7) ** 2) ** 0.5
        if km < best_km:
            best, best_km = name, km
    return best


LIFT_WORDS = re.compile(r"\b(lift|chair|chairlift|express|quad|triple|double|six|high speed|detachable|the)\b")


def name_key(name: str) -> str:
    """'Bridger Lift' / 'Bridger' -> 'bridger'; "Pierre's Knob Chair" -> 'pierresknob'."""
    words = LIFT_WORDS.sub(" ", name.lower().replace("’", "'"))
    return re.sub(r"[^a-z0-9]", "", words)


def parse_overpass(body: dict) -> list[Aerialway]:
    lifts = []
    for element in body.get("elements", []):
        geometry = element.get("geometry") or []
        tags = element.get("tags") or {}
        if element.get("type") != "way" or len(geometry) < 2:
            continue
        lifts.append(Aerialway(
            name=tags.get("name") or tags.get("ref") or "Unnamed lift",
            kind=tags.get("aerialway", "lift"),
            lat=tuple(p["lat"] for p in geometry),
            lon=tuple(p["lon"] for p in geometry),
        ))
    return lifts


class AerialwayCache:
    """Lifts per area, fetched from Overpass at most once a week per area."""

    def __init__(self) -> None:
        self._areas: dict[tuple, tuple[float, list[Aerialway]]] = {}

    async def around(self, client: httpx.AsyncClient, lats: list[float], lons: list[float]) -> list[Aerialway]:
        """Lifts around a track. Days at a known resort share one box around the whole ski
        area (one lookup per resort); elsewhere the box is snapped to a 0.05° grid."""
        mid_lat, mid_lon = sorted(lats)[len(lats) // 2], sorted(lons)[len(lons) // 2]
        resort = resort_near(mid_lat, mid_lon)
        if resort:
            clat, clon = RESORT_CENTERS[resort]
            box = (round(clat - RESORT_BOX, 3), round(clon - RESORT_BOX, 3), round(clat + RESORT_BOX, 3), round(clon + RESORT_BOX, 3))
        else:
            box = (
                math.floor(min(lats) / 0.05) * 0.05, math.floor(min(lons) / 0.05) * 0.05,
                math.ceil(max(lats) / 0.05) * 0.05, math.ceil(max(lons) / 0.05) * 0.05,
            )
        cached = self._areas.get(box)
        if cached and time.time() - cached[0] < CACHE_SECONDS:
            return cached[1]
        south, west, north, east = box
        query = f'[out:json][timeout:25];way["aerialway"]["aerialway"!~"{NOT_LIFTS}"]({south},{west},{north},{east});out geom tags;'
        lifts = parse_overpass(await overpass(client, query))
        log.info("Loaded %d lifts from OpenStreetMap", len(lifts))
        self._areas[box] = (time.time(), lifts)
        return lifts


async def overpass(client: httpx.AsyncClient, query: str) -> dict:
    """Run a query, retrying once per server and falling back to the mirror (public
    Overpass servers are busy and sometimes answer 429 or 502)."""
    error: Exception | None = None
    for url in OVERPASS_URLS:
        for attempt in range(2):
            try:
                response = await client.post(url, data={"data": query}, timeout=60)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as e:
                error = e
                await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError(f"OpenStreetMap lift lookup failed: {error!r}")
