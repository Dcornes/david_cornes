"""Lift vertical vs ride time for the website, plus your real lap times from Strava.

The fetching lives in lifts/lift_vertical (installed like snow/snow_report). To keep requests to
other sites (and CPU) down, everything is cached:
- Lift Blog data: fetched when asked for and over a week old, kept in the database.
- Your Strava days: kept in the database; Strava is checked for new days once a day.
- Lift and run maps: shipped with the code; OpenStreetMap is asked monthly, or after new ski days.
- Heatmaps: worked out once per resort and snow threshold until the data changes.
Responses also tell browsers how long they can reuse them.
"""

import asyncio
import functools
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from lift_vertical import heatmap
from lift_vertical.aerialways import RESORT_CENTERS
from lift_vertical.day_store import PostgresDays
from lift_vertical.liftblog import fetch_lifts
from lift_vertical.my_laps import DEFAULT_CACHE, MyLapsCache, run_my_laps_updates
from lift_vertical.routes import RouteCache, run_route_updates
from lift_vertical.runs import load_snapshot

from app.stored import StoredValue

LIFT_DATA_MAX_AGE = 7 * 24 * 60 * 60  # lift specs change maybe once a season
STRAVA_CHECK_SECONDS = 24 * 60 * 60
MAP_REFRESH_SECONDS = 30 * 24 * 60 * 60
DAY = 24 * 60 * 60


async def fetch_lift_data(previous: dict | None) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        return await fetch_lifts(client)


lift_data = StoredValue("liftblog", LIFT_DATA_MAX_AGE, fetch_lift_data)
# Saved analysed days, so restarts don't redo them (LAP_CACHE_FILE overrides the path).
my_laps = MyLapsCache(Path(os.environ.get("LAP_CACHE_FILE", DEFAULT_CACHE)))

routes = RouteCache(DEFAULT_CACHE.parent / "lift_routes.json")  # saved lift and run maps (public OSM data)
route_refresh = asyncio.Event()  # set when new Strava ski days arrive, to refresh the maps early

router = APIRouter(prefix="/api/lifts", tags=["lifts"])


def cacheable(response: Response, seconds: int) -> None:
    response.headers["Cache-Control"] = f"public, max-age={seconds}"


@router.get("")
async def lift_vertical(response: Response) -> dict:
    """Operating lifts at each ski area with vertical rise, ride time and vertical feet per minute."""
    data = await lift_data.get()
    if data is None:
        raise HTTPException(503, "Lift data hasn't been fetched yet; try again in a minute.")
    cacheable(response, 3600)
    return data


@router.get("/mine")
def my_lap_times(response: Response) -> dict:
    """Per-lift medians of your real rides and laps (from Strava). Totals only: no tracks or dates."""
    if my_laps.latest is None:
        raise HTTPException(404, "No lap times yet.")
    cacheable(response, 3600)
    return my_laps.latest


@router.get("/routes")
def lift_routes(response: Response) -> dict:
    """Each resort's lift lines, and a ski route from each lift's top back to its bottom (from OpenStreetMap)."""
    if routes.latest is None:
        raise HTTPException(503, "Lift maps haven't loaded yet; try again in a few minutes.")
    cacheable(response, DAY)
    return routes.latest


snow_history = heatmap.SnowHistory()  # new snow per ski day, kept in the database


@functools.cache
def heatmap_shapes() -> dict[str, list[dict]]:
    """resort -> every run's outline (from the shipped snapshot), for every mapped resort."""
    return {resort: heatmap.run_shapes(pistes) for resort, pistes in load_snapshot().items()}


@router.get("/heatmap/runs")
def heatmap_runs(response: Response, resort: str = "Big Sky") -> dict:
    """A resort's runs and lift lines for the map (public OpenStreetMap data)."""
    shapes = heatmap_shapes()
    if resort not in shapes:
        raise HTTPException(404, f"No run map for {resort}.")
    resort_map = next((r for r in (routes.latest or {}).get("resorts", []) if r["resort"] == resort), None)
    lifts = [{"name": l["name"], "line": l["line"]} for l in resort_map["lifts"]] if resort_map else []
    cacheable(response, DAY)
    return {"resort": resort, "resorts": sorted(shapes), "center": RESORT_CENTERS[resort], "runs": shapes[resort], "lifts": lifts}


_heatmaps: dict[tuple, dict] = {}  # (resort, min_snow, data version) -> response


@router.get("/heatmap")
async def powder_heatmap(response: Response, resort: str = "Big Sky", min_snow: float = Query(8, ge=0, le=60)) -> dict:
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
            await snow_history.ensure(client, resort, dates)  # only fetches dates it hasn't stored
    except httpx.HTTPError as error:
        raise HTTPException(503, f"Couldn't load snow history; try again shortly ({error.__class__.__name__}).")
    # The same answer until a ski day or a snow total is added.
    version = (len(dates), sum(len(d.heat or []) for d in days if d.resort == resort), len(snow_history.snow))
    key = (resort, min_snow, version)
    if key not in _heatmaps:
        if len(_heatmaps) > 200:
            _heatmaps.clear()
        _heatmaps[key] = heatmap.heatmap(days, resort, min_snow, snow_history)
    cacheable(response, 3600)
    return _heatmaps[key]


async def run_my_laps() -> None:
    my_laps.store = PostgresDays.from_env()  # read here: backend/.env is loaded after this module
    snow_history.store = my_laps.store  # daily new snow is kept in the same database
    await lift_data.get()  # Lift Blog names, for matching lifts (from the database when cached)
    await run_my_laps_updates(my_laps, lambda: lift_data.data, refresh=STRAVA_CHECK_SECONDS,
                              on_new_days=lambda count: route_refresh.set())


async def run_routes() -> None:
    # Starts from the shipped snapshot (or saved maps), refreshes monthly, and sooner on new Strava days.
    await run_route_updates(routes, refresh=MAP_REFRESH_SECONDS, wake=route_refresh)
