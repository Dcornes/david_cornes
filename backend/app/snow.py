"""Ski area snow totals and webcams for the website.

The fetching lives in snow/snow_report (installed like collector/market_data). Nothing runs in
the background: a request finds the cached report (in memory, or in the database after a
restart) and, if it's over an hour old, refreshes it once in the background. So the resorts'
sites are only asked when someone is looking, at most hourly.
"""

import httpx
from fastapi import APIRouter, HTTPException, Response
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


@router.get("")
async def snow_totals(response: Response) -> dict:
    """Each resort's summary stats, webcams and detail tables, at most an hour old."""
    data = await report.get()
    if data is None:
        raise HTTPException(503, "Snow totals haven't been fetched yet; try again in a minute.")
    response.headers["Cache-Control"] = "public, max-age=300"  # browsers reuse it for 5 minutes
    return data
