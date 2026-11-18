"""Lift lines and a ski route from each lift's top back to its bottom, for the route map.

For each resort, OpenStreetMap (via Overpass) gives every lift line and every mapped
downhill ski run. The runs become a graph (run vertices joined where runs share or nearly
share a point), and the shortest path from near a lift's top to near its bottom is that
lift's lap route. OSM lift lines are drawn bottom to top by convention. Nothing here
comes from anyone's GPS tracks.

run_route_updates refreshes a RouteCache weekly (the backend serves it at /api/lifts/routes).
Run from the lifts/ directory to print each resort's routes:

    python -m lift_vertical.routes
"""

import asyncio
import heapq
import json
import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .aerialways import NOT_LIFTS, RESORT_BOX, RESORT_CENTERS, name_key, overpass

log = logging.getLogger(__name__)

REFRESH_SECONDS = 7 * 24 * 60 * 60
RETRY_SECONDS = 60 * 60
JOIN_M = 25  # run ends this close to another run's point are treated as connected
STATION_M = 120  # how far from a lift's top/bottom a run may start/end
MAX_POINTS = 120  # simplify long routes for the page
MIN_ROUTE_SHARE = 0.5  # a route shorter than half the lift line is a mapping artifact

# OSM piste:difficulty, easiest first; a route is as hard as its hardest run.
DIFFICULTY = ["novice", "easy", "intermediate", "advanced", "expert", "freeride", "extreme"]
DIFFICULTY_LABELS = {"novice": "Beginner", "easy": "Easy", "intermediate": "Intermediate", "advanced": "Advanced",
                     "expert": "Expert", "freeride": "Expert (freeride)", "extreme": "Extreme"}


def query(resort: str) -> str:
    lat, lon = RESORT_CENTERS[resort]
    box = f"{lat - RESORT_BOX},{lon - RESORT_BOX},{lat + RESORT_BOX},{lon + RESORT_BOX}"
    return (
        f'[out:json][timeout:60];('
        f'way["aerialway"]["aerialway"!~"{NOT_LIFTS}"]({box});'
        f'way["piste:type"="downhill"]({box});'
        f');out geom tags;'
    )


class Plane:
    """Lat/lon to meters on a flat plane around a resort."""

    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0, self.lon0, self.kx = lat0, lon0, 111_320 * math.cos(math.radians(lat0))

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * 110_540


def run_graph(runs: list[dict], plane: Plane):
    """Vertices (lat, lon, x, y, run name, difficulty) and neighbours with distances in meters."""
    vertices: list[tuple[float, float, float, float, str, str]] = []
    index: dict[tuple[float, float], int] = {}
    edges: dict[int, list[tuple[int, float]]] = defaultdict(list)
    ends: list[int] = []

    def vertex(lat: float, lon: float, name: str, difficulty: str) -> int:
        key = (round(lat, 6), round(lon, 6))  # runs that share an OSM node share coordinates
        if key not in index:
            index[key] = len(vertices)
            vertices.append((lat, lon, *plane.xy(lat, lon), name, difficulty))
        return index[key]

    for run in runs:
        ids = [vertex(p["lat"], p["lon"], run["name"], run["difficulty"]) for p in run["geometry"]]
        for a, b in zip(ids, ids[1:]):
            d = math.dist(vertices[a][2:4], vertices[b][2:4])
            edges[a].append((b, d))
            edges[b].append((a, d))
        ends += [ids[0], ids[-1]]

    # Join run ends to any nearby vertex on another run (runs don't always share nodes).
    cell = JOIN_M
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, v in enumerate(vertices):
        grid[(int(v[2] // cell), int(v[3] // cell))].append(i)
    for e in ends:
        ex, ey = vertices[e][2:4]
        gx, gy = int(ex // cell), int(ey // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid[(gx + dx, gy + dy)]:
                    if j != e:
                        d = math.dist((ex, ey), vertices[j][2:4])
                        if d <= JOIN_M:
                            edges[e].append((j, d))
                            edges[j].append((e, d))
    return vertices, edges


def shortest_route(vertices, edges, start_xy: tuple[float, float], end_xy: tuple[float, float]) -> list[int] | None:
    """Shortest path over the runs from any vertex near the start to any vertex near the end.
    The walk from the station to the first/last vertex counts toward the length."""
    near_start = [(math.dist(start_xy, v[2:4]), i) for i, v in enumerate(vertices) if math.dist(start_xy, v[2:4]) <= STATION_M]
    targets = {i: math.dist(end_xy, v[2:4]) for i, v in enumerate(vertices) if math.dist(end_xy, v[2:4]) <= STATION_M}
    if not near_start or not targets:
        return None
    best = {i: d for d, i in near_start}
    previous: dict[int, int] = {}
    heap = [(d, i) for d, i in near_start]
    heapq.heapify(heap)
    finish, finish_cost = None, math.inf
    while heap:
        d, i = heapq.heappop(heap)
        if d > best.get(i, math.inf) or d >= finish_cost:
            continue
        if i in targets and d + targets[i] < finish_cost:
            finish, finish_cost = i, d + targets[i]
        for j, w in edges.get(i, []):
            nd = d + w
            if nd < best.get(j, math.inf):
                best[j], previous[j] = nd, i
                heapq.heappush(heap, (nd, j))
    if finish is None:
        return None
    path = [finish]
    while path[-1] in previous:
        path.append(previous[path[-1]])
    return path[::-1]


def simplify(points: list[list[float]], limit: int = MAX_POINTS) -> list[list[float]]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def resort_routes(resort: str, body: dict) -> dict:
    """The resort's lifts, each with its line and (when one is found) a run back to its bottom."""
    lat0, lon0 = RESORT_CENTERS[resort]
    plane = Plane(lat0, lon0)
    lifts, runs = [], []
    for element in body.get("elements", []):
        tags, geometry = element.get("tags") or {}, element.get("geometry") or []
        if element.get("type") != "way" or len(geometry) < 2:
            continue
        if "aerialway" in tags:
            lifts.append({"name": tags.get("name") or tags.get("ref") or "Unnamed lift", "geometry": geometry})
        elif tags.get("piste:type") == "downhill" and tags.get("area") != "yes":
            runs.append({"name": tags.get("piste:name") or tags.get("name") or "", "geometry": geometry,
                         "difficulty": tags.get("piste:difficulty", "")})
    vertices, edges = run_graph(runs, plane)

    result = []
    for lift in lifts:
        bottom, top = lift["geometry"][0], lift["geometry"][-1]
        path = shortest_route(vertices, edges, plane.xy(top["lat"], top["lon"]), plane.xy(bottom["lat"], bottom["lon"]))
        route = None
        lift_length = sum(
            math.dist(plane.xy(a["lat"], a["lon"]), plane.xy(b["lat"], b["lon"])) for a, b in zip(lift["geometry"], lift["geometry"][1:])
        )
        if path:
            points = [[vertices[i][0], vertices[i][1]] for i in path]
            length = sum(math.dist(vertices[a][2:4], vertices[b][2:4]) for a, b in zip(path, path[1:]))
            names = []
            for i in path:
                name = vertices[i][4]
                if name and (not names or names[-1] != name):
                    names.append(name)
            levels = [vertices[i][5] for i in path if vertices[i][5] in DIFFICULTY]
            hardest = max(levels, key=DIFFICULTY.index) if levels else None
            if length >= MIN_ROUTE_SHARE * lift_length:
                route = {"points": simplify(points), "length_m": round(length), "runs": names,
                         "difficulty": DIFFICULTY_LABELS.get(hardest) if hardest else None}
        result.append({
            "name": lift["name"],
            "key": name_key(lift["name"]),
            "line": [[p["lat"], p["lon"]] for p in lift["geometry"]],
            "route": route,
        })
    return {"resort": resort, "center": [lat0, lon0], "lifts": result}


async def fetch_routes(client: httpx.AsyncClient) -> dict:
    resorts, failed = [], []
    for resort in RESORT_CENTERS:
        try:
            resorts.append(resort_routes(resort, await overpass(client, query(resort))))
        except RuntimeError as error:
            log.warning("Couldn't load %s lifts and runs: %s", resort, error)
            failed.append(resort)
        await asyncio.sleep(2)  # one query at a time, gently, on the public server
    if not resorts:
        raise RuntimeError("No resort maps loaded")
    return {"fetched_at": datetime.now(timezone.utc).isoformat(), "resorts": resorts, "failed": failed}


class RouteCache:
    """The latest maps. With a file, they're saved after each load and reused at start
    while less than a week old, so a restart doesn't query Overpass again."""

    def __init__(self, file: Path | None = None) -> None:
        self.latest: dict | None = None
        self.file = file
        self.loaded_at = 0.0
        if file and file.exists():
            try:
                self.latest = json.loads(file.read_text(encoding="utf-8"))
                self.loaded_at = file.stat().st_mtime
            except ValueError:
                log.warning("Ignoring an unreadable route cache")

    def save(self) -> None:
        if self.file and self.latest:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.latest), encoding="utf-8")
            self.loaded_at = time.time()


async def run_route_updates(cache: RouteCache, refresh: float = REFRESH_SECONDS) -> None:
    """Load every resort's lifts and runs now and then weekly, until cancelled."""
    async with httpx.AsyncClient(timeout=70, headers={"User-Agent": "davidcornes.com lift routes"}) as client:
        while True:
            age = time.time() - cache.loaded_at
            if cache.latest and age < refresh:
                await asyncio.sleep(refresh - age)  # saved maps are still fresh
                continue
            try:
                cache.latest = await fetch_routes(client)
                cache.save()
                wait = refresh
            except Exception:
                log.exception("Couldn't load lift routes; retrying in %ds", RETRY_SECONDS)
                wait = min(refresh, RETRY_SECONDS)
            await asyncio.sleep(wait)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    async def run() -> dict:
        async with httpx.AsyncClient(timeout=70, headers={"User-Agent": "davidcornes.com lift routes"}) as client:
            return await fetch_routes(client)

    data = asyncio.run(run())
    for resort in data["resorts"]:
        with_route = [l for l in resort["lifts"] if l["route"]]
        print(f"{resort['resort']}: {len(resort['lifts'])} lifts, {len(with_route)} with a route")
        for lift in with_route[:8]:
            print(f"  {lift['name']}: {lift['route']['length_m']} m via {', '.join(lift['route']['runs'][:4]) or 'unnamed runs'}")


if __name__ == "__main__":
    main()
