"""Powder-day run heatmap: how often you skied each run on days with lots of new snow.

New snow for a ski day comes from a SNOTEL station on the mountain: the biggest rise in snow
depth over the 48 hours to 7 am (Mountain time) that morning, from hourly readings smoothed over
three hours. Depth settles as snow falls, so this undercounts what the resort reports; over
24 hours Lone Mountain only shows 8"+ once or twice a season, over 48 hours four to six times.
Bridger Bowl publishes its daily snow reports (since December 2020), so there the 48-hour total
from that morning's report is used; SNOTEL (Brackett Creek, or Sacajawea for seasons Brackett
Creek has no readings) only fills in older days.

Published: per-run counts and a heat grid summed over the chosen days. No dates and no raw GPS.
"""

import asyncio
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx

from .day_store import SkiDay
from .routes import simplify
from .aerialways import RESORT_CENTERS
from .runs import HEAT_CELL_M, Piste

log = logging.getLogger(__name__)

AWDB_URL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
STATION_PAGE = "https://wcc.sc.egov.usda.gov/nwcc/site?sitenum={number}"
# resort -> SNOTEL stations as (triplet, name, elevation in feet), best first. A season the first
# station has no readings for (Brackett Creek has none for 2023-24) comes from the next.
SNOW_STATIONS = {
    "Big Sky": [("590:MT:SNTL", "Lone Mountain", 8820)],
    "Bridger Bowl": [("365:MT:SNTL", "Brackett Creek", 7370), ("929:MT:SNTL", "Sacajawea", 6610)],
}
BRIDGER_API = "https://api.bridgerbowl.com/graphql"
BRIDGER_SOURCE = "Bridger Bowl snow report (48 h)"
MORNING_PST = 6  # SNOTEL hours are Pacific Standard Time: 6 am PST is 7 am in Montana
WINDOW_HOURS = 48  # storms often run over two nights; resorts report 48-hour totals too
MIN_READINGS = 24  # of the 49 hourly readings in the window, or the day is unknown
MAX_RUN_POINTS = 40


def season_of(day: date) -> int:
    return day.year if day.month >= 8 else day.year - 1


def new_snow(depths: dict[datetime, float], day: date) -> float | None:
    """Inches of new snow in the WINDOW_HOURS to the morning of `day` (None without enough data):
    the biggest rise in smoothed snow depth over that stretch."""
    end = datetime(day.year, day.month, day.day, MORNING_PST)
    hours = [end - timedelta(hours=h) for h in range(WINDOW_HOURS, -1, -1)]
    values = [depths[t] for t in hours if t in depths]
    if len(values) < MIN_READINGS:
        return None
    smoothed = [statistics.median(values[max(0, i - 1): i + 2]) for i in range(len(values))]
    lowest, rise = smoothed[0], 0.0
    for value in smoothed:
        lowest = min(lowest, value)
        rise = max(rise, value - lowest)
    return rise


class SnowHistory:
    """New snow per (resort, date), fetched from SNOTEL a season at a time and kept: in the
    database too when there is one (the snow_days table), so restarts don't fetch it again."""

    def __init__(self, store=None) -> None:
        self.snow: dict[tuple[str, str], float | None] = {}
        self.store = store  # PostgresDays, or None
        self._loaded = False
        self._lock = asyncio.Lock()

    async def ensure(self, client: httpx.AsyncClient, resort: str, dates: list[str]) -> None:
        if resort not in SNOW_STATIONS:
            return
        async with self._lock:
            if self.store and not self._loaded:
                try:
                    self.snow.update(await asyncio.to_thread(self.store.load_snow, WINDOW_HOURS))
                    self._loaded = True
                except Exception as error:  # noqa: BLE001 - the database is optional
                    log.warning("Couldn't load snow history from the database (%s)", error)
            missing = sorted({d for d in dates if (resort, d) not in self.snow})
            fetched: dict[tuple[str, str], tuple[float | None, str]] = {}  # -> (inches, station)
            if resort == "Bridger Bowl" and missing:
                reports = await fetch_bridger_reports(client)
                for d in missing:
                    if d in reports:
                        fetched[(resort, d)] = (reports[d], BRIDGER_SOURCE)
                missing = [d for d in missing if (resort, d) not in fetched]
            seasons: dict[int, list[date]] = defaultdict(list)
            for d in missing:
                seasons[season_of(date.fromisoformat(d))].append(date.fromisoformat(d))
            for days in seasons.values():
                for triplet, name, _ in SNOW_STATIONS[resort]:
                    depths = await fetch_depths(client, triplet, min(days) - timedelta(days=3), max(days))
                    if depths:
                        break  # this station has readings for the season
                    await asyncio.sleep(1)
                for day in days:
                    fetched[(resort, day.isoformat())] = (new_snow(depths, day), f"{name} SNOTEL ({triplet})")
                await asyncio.sleep(1)  # be gentle with the SNOTEL service
            self.snow.update({key: inches for key, (inches, _) in fetched.items()})
            # Keep settled days; the last few may still get late readings, so they're fetched again.
            recent = (date.today() - timedelta(days=3)).isoformat()
            keep = {key: value for key, value in fetched.items() if key[1] < recent}
            if self.store and keep:
                try:
                    await asyncio.to_thread(self.store.save_snow, WINDOW_HOURS, keep)
                    log.info("Saved new snow for %d %s days to the database", len(keep), resort)
                except Exception as error:  # noqa: BLE001
                    log.warning("Couldn't save snow history to the database (%s)", error)

    def get(self, resort: str, day: str | None) -> float | None:
        return self.snow.get((resort, day)) if day else None


async def fetch_bridger_reports(client: httpx.AsyncClient) -> dict[str, float]:
    """Date -> new snow in the last 48 hours, from Bridger Bowl's first report of each morning."""
    reports: list[dict] = []
    page, last = 1, 1
    while page <= last:
        query = f"{{ snow_reports(first: 1000, page: {page}) {{ paginatorInfo {{ lastPage }} data {{ date recent_snow }} }} }}"
        response = await client.post(BRIDGER_API, json={"query": query}, timeout=60)
        response.raise_for_status()
        body = response.json()["data"]["snow_reports"]
        reports += body["data"]
        last = body["paginatorInfo"]["lastPage"]
        page += 1
    return morning_reports(reports)


def morning_reports(reports: list[dict]) -> dict[str, float]:
    """The first report before 10 am each day (later ones are updated through the day)."""
    result: dict[str, float] = {}
    for report in sorted(reports, key=lambda r: r["date"]):
        day, hour = report["date"][:10], int(report["date"][11:13])
        if hour < 10 and day not in result and report.get("recent_snow") is not None:
            result[day] = float(report["recent_snow"])
    return result


async def fetch_depths(client: httpx.AsyncClient, triplet: str, begin: date, end: date) -> dict[datetime, float]:
    params = {"stationTriplets": triplet, "elements": "SNWD", "duration": "HOURLY",
              "beginDate": f"{begin:%Y-%m-%d} 00:00", "endDate": f"{end:%Y-%m-%d} 12:00"}
    for attempt in range(3):  # the service sometimes answers 403 or 5xx to a quick run of requests
        response = await client.get(AWDB_URL, params=params, timeout=60)
        if response.status_code not in (403, 429) and response.status_code < 500:
            break
        await asyncio.sleep(5 * (attempt + 1))
    response.raise_for_status()
    depths = {}
    for station in response.json():
        for element in station.get("data", []):
            for point in element.get("values", []):
                if point.get("value") is not None:
                    depths[datetime.fromisoformat(point["date"])] = float(point["value"])
    return depths


def run_shapes(pistes: list[Piste]) -> list[dict]:
    """Every run's outline for the map (simplified), without counts."""
    return [{"id": p.id, "name": p.name, "difficulty": p.difficulty, "area": p.area,
             "points": simplify([list(pt) for pt in p.points], MAX_RUN_POINTS)} for p in pistes]


def heatmap(days: list[SkiDay], resort: str, min_snow: float, snow: SnowHistory) -> dict:
    """Run counts on the resort's days with at least `min_snow` inches of new snow."""
    with_runs = [d for d in days if d.resort == resort and d.runs is not None and d.rides]
    amounts = [(d, snow.get(resort, d.date)) for d in with_runs]
    chosen = [d for d, inches in amounts if inches is not None and inches >= min_snow]
    counts: dict[str, int] = defaultdict(int)
    cells: dict[tuple[int, int], int] = defaultdict(int)
    for day in chosen:
        for run, n in day.runs.items():
            counts[run] += n
        for x, y, n in day.heat or []:
            cells[(x, y)] += n
    stations = [{"name": f"{name} SNOTEL", "elevation_ft": elevation, "url": STATION_PAGE.format(number=triplet.split(":")[0])}
                for triplet, name, elevation in SNOW_STATIONS.get(resort, [])]
    return {
        "resort": resort,
        "min_snow": min_snow,
        # Where new snow comes from: the resort's own reports, when it publishes them.
        "snow_source": ({"name": "Bridger Bowl's morning snow report", "url": "https://bridgerbowl.com/weather"}
                        if resort == "Bridger Bowl" else None),
        "station": stations[0] if stations else None,
        "backup_stations": stations[1:],
        "days_with_runs": len(with_runs),
        "days_unknown_snow": sum(1 for _, inches in amounts if inches is None),
        "days": len(chosen),
        # New snow on each of your days here, largest first (no dates), for the threshold control.
        "snow_amounts": sorted((round(i, 1) for _, i in amounts if i is not None), reverse=True),
        "descents": sum(counts.values()),
        "counts": dict(counts),
        # Where I went on those days, summed: [x, y, passes] per grid cell, x east and y north in
        # cells of cell_m meters from center.
        "heat": {"cell_m": HEAT_CELL_M, "center": RESORT_CENTERS.get(resort),
                 "cells": [[x, y, n] for (x, y), n in cells.items()]},
    }
