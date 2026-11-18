"""Ski area snow totals and webcams for the website.

The fetching lives in snow/snow_report (installed like collector/market_data).
main.py runs run_snow_updates in the background, which refreshes the cache hourly.
"""

from fastapi import APIRouter, HTTPException
from snow_report.resorts import SnowCache, run_snow_updates

cache = SnowCache()

router = APIRouter(prefix="/api/snow", tags=["snow"])


@router.get("")
def snow_totals() -> dict:
    """Each resort's summary stats, webcams and detail tables, refreshed hourly."""
    if cache.latest is None:
        raise HTTPException(503, "Snow totals haven't been fetched yet; try again in a minute.")
    return cache.latest


async def run_snow() -> None:
    await run_snow_updates(cache)
