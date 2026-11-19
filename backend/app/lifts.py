"""Lift vertical vs ride time for the website, plus your real lap times from Strava.

The fetching lives in lifts/lift_vertical (installed like snow/snow_report).
main.py runs run_lifts in the background (Lift Blog data, refreshed daily) and
run_my_laps (Strava, every few hours; only when the STRAVA_ variables are set).
"""

import asyncio
import functools
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query
from lift_vertical import heatmap
from lift_vertical.aerialways import RESORT_CENTERS
from lift_vertical.runs import load_snapshot
from lift_vertical.liftblog import LiftCache, run_lift_updates
from lift_vertical.day_store import PostgresDays
from lift_vertical.my_laps import DEFAULT_CACHE, MyLapsCache, run_my_laps_updates
from lift_vertical.routes import RouteCache, run_route_updates

cache = LiftCache()
# Saved analysed days, so restarts don't redo them (LAP_CACHE_FILE overrides the path).
my_laps = MyLapsCache(Path(os.environ.get("LAP_CACHE_FILE", DEFAULT_CACHE)))

routes = RouteCache(DEFAULT_CACHE.parent / "lift_routes.json")  # saved lift and run maps (public OSM data)
route_refresh = asyncio.Event()  # set when new Strava ski days arrive, to refresh the maps early

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


snow_history = heatmap.SnowHistory()  # new snow per ski day, from SNOTEL


@functools.cache
def heatmap_shapes() -> dict[str, list[dict]]:
    """resort -> every run's outline, for resorts with a snow station (from the shipped snapshot)."""
    pistes = load_snapshot()
    return {resort: heatmap.run_shapes(pistes[resort]) for resort in heatmap.SNOW_STATIONS if resort in pistes}


@router.get("/heatmap/runs")
def heatmap_runs(resort: str = "Big Sky") -> dict:
    """A resort's runs and lift lines for the powder heatmap (public OpenStreetMap data)."""
    shapes = heatmap_shapes()
    if resort not in shapes:
        raise HTTPException(404, f"No run map for {resort}.")
    resort_map = next((r for r in (routes.latest or {}).get("resorts", []) if r["resort"] == resort), None)
    lifts = [{"name": l["name"], "line": l["line"]} for l in resort_map["lifts"]] if resort_map else []
    return {"resort": resort, "resorts": sorted(shapes), "center": RESORT_CENTERS[resort], "runs": shapes[resort], "lifts": lifts}


@router.get("/heatmap")
async def powder_heatmap(resort: str = "Big Sky", min_snow: float = Query(8, ge=0, le=60)) -> dict:
    """How many times you skied each run on days with at least `min_snow` inches of new snow.
    Counts only: no dates or GPS."""
    if resort not in heatmap.SNOW_STATIONS:
        raise HTTPException(404, f"No snow station for {resort}.")
    days = list(my_laps.days.values())
    dates = [d.date for d in days if d.resort == resort and d.date and d.runs is not None]
    if not dates:
        raise HTTPException(404, "No ski days with runs yet.")
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "davidcornes.com snow heatmap"}) as client:
            await snow_history.ensure(client, resort, dates)
    except httpx.HTTPError as error:
        raise HTTPException(503, f"Couldn't load snow history; try again shortly ({error.__class__.__name__}).")
    return heatmap.heatmap(days, resort, min_snow, snow_history)


async def run_lifts() -> None:
    await run_lift_updates(cache)


async def run_my_laps() -> None:
    await asyncio.sleep(5)  # let the Lift Blog data load first, for matching lift names
    my_laps.store = PostgresDays.from_env()  # read here: backend/.env is loaded after this module
    snow_history.store = my_laps.store  # daily new snow is kept in the same database
    await run_my_laps_updates(my_laps, lambda: cache.latest, on_new_days=lambda count: route_refresh.set())


async def run_routes() -> None:
    # Starts from the shipped snapshot (or saved maps), refreshes daily, and sooner on new Strava days.
    await run_route_updates(routes, wake=route_refresh)
