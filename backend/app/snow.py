"""Ski area snow totals and webcams for the website.

The fetching lives in snow/snow_report (installed like collector/market_data). Nothing runs in
the background: a request finds the cached report (in memory, or in the database after a
restart) and, if it's over an hour old, refreshes it once in the background. So the resorts'
sites are only asked when someone is looking, at most hourly.
"""

import httpx
from fastapi import APIRouter, HTTPException, Response
from lift_vertical.heatmap import recent_snowfall
from snow_report.resorts import RESORTS, SnowCache, update

from app.stored import StoredValue

MAX_AGE = 60 * 60
cache = SnowCache()


async def fetch(previous: dict | None) -> dict | None:
    """Fetch every resort; one that fails keeps its previous result (marked stale)."""
    if previous and not cache.resorts:
        cache.resorts = {r["id"]: r for r in previous["resorts"]}
        cache.fetched_at = previous["fetched_at"]
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "davidcornes.com snow page"}) as client:
        succeeded = await update(cache, client, RESORTS)
    return cache.latest if succeeded else None


report = StoredValue("snow_report", MAX_AGE, fetch)

router = APIRouter(prefix="/api/snow", tags=["snow"])


RECENT_MAX_AGE = 3 * 60 * 60
NAMES = {module.ID: module.NAME for module in RESORTS}
NAMES["big-sky"] = "Big Sky"  # the snow report says "Big Sky Resort"; the stations know "Big Sky"
_recent: dict[str, StoredValue] = {}


def recent_for(slug: str) -> StoredValue:
    if slug not in _recent:
        async def fetch(previous: dict | None) -> dict:
            async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "davidcornes.com snow page"}) as client:
                return {"resort": slug, **await recent_snowfall(client, NAMES[slug])}

        _recent[slug] = StoredValue(f"snow_recent:{slug}", RECENT_MAX_AGE, fetch)
        _recent[slug].enabled = report.enabled
    return _recent[slug]


@router.get("/recent")
async def recent_snow(response: Response, resort: str) -> dict:
    """New snow on each of the last 7 mornings at a resort (by id, e.g. bridger-bowl), at most 3 hours old."""
    if resort not in NAMES:
        raise HTTPException(404, f"No resort {resort}.")
    data = await recent_for(resort).get()
    if data is None:
        raise HTTPException(503, "Recent snowfall isn't available yet; try again shortly.")
    response.headers["Cache-Control"] = "public, max-age=1800"
    return data


@router.get("")
async def snow_totals(response: Response) -> dict:
    """Each resort's summary stats, webcams and detail tables, at most an hour old."""
    data = await report.get()
    if data is None:
        raise HTTPException(503, "Snow totals haven't been fetched yet; try again in a minute.")
    response.headers["Cache-Control"] = "public, max-age=300"  # browsers reuse it for 5 minutes
    return data
