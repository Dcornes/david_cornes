"""Lift vertical vs ride time for the website, plus your real lap times from Strava.

The fetching lives in lifts/lift_vertical (installed like snow/snow_report).
main.py runs run_lifts in the background (Lift Blog data, refreshed daily) and
run_my_laps (Strava, every few hours; only when the STRAVA_ variables are set).
"""

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from lift_vertical.liftblog import LiftCache, run_lift_updates
from lift_vertical.my_laps import DEFAULT_CACHE, MyLapsCache, run_my_laps_updates
from lift_vertical.routes import RouteCache, run_route_updates

cache = LiftCache()
# Saved analysed days, so restarts don't redo them (LAP_CACHE_FILE overrides the path).
my_laps = MyLapsCache(Path(os.environ.get("LAP_CACHE_FILE", DEFAULT_CACHE)))

routes = RouteCache(DEFAULT_CACHE.parent / "lift_routes.json")  # saved lift and run maps (public OSM data)

router = APIRouter(prefix="/api/lifts", tags=["lifts"])


@router.get("")
def lift_vertical() -> dict:
    """Operating lifts at each ski area with vertical rise, ride time and vertical feet per minute."""
    if cache.latest is None:
        raise HTTPException(503, "Lift data hasn't been fetched yet; try again in a minute.")
    return cache.latest


@router.get("/mine")
def my_lap_times() -> dict:
    """Per-lift medians of your real rides and laps (from Strava). Totals only: no tracks or dates."""
    if my_laps.latest is None:
        raise HTTPException(404, "No lap times yet.")
    return my_laps.latest


@router.get("/routes")
def lift_routes() -> dict:
    """Each resort's lift lines, and a ski route from each lift's top back to its bottom (from OpenStreetMap)."""
    if routes.latest is None:
        raise HTTPException(503, "Lift maps haven't loaded yet; try again in a few minutes.")
    return routes.latest


async def run_lifts() -> None:
    await run_lift_updates(cache)


async def run_my_laps() -> None:
    await asyncio.sleep(5)  # let the Lift Blog data load first, for matching lift names
    await run_my_laps_updates(my_laps, lambda: cache.latest)


async def run_routes() -> None:
    await asyncio.sleep(60)  # after the Strava pass's first map lookups, to spread out Overpass queries
    await run_route_updates(routes)
