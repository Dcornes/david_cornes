"""Which ski runs you skied: match each descent's GPS to OpenStreetMap's runs (pistes).

Between one lift ride and the next you're skiing down. Each moving GPS point in that stretch
is matched to the nearest run line within RUN_M, or failing that to a run area (a bowl or
glade mapped as an outline) it falls inside. A run counts once per descent when you spent at
least MIN_ON_RUN_S on it. Only these counts are kept, never the GPS points.

The runs come from a snapshot shipped with the code (data/pistes.json), so their ids stay the
same between days. Refresh it with `python -m lift_vertical.runs --snapshot` from lifts/.
"""

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .aerialways import RESORT_BOX, RESORT_CENTERS, overpass
from .laps import Projector, Ride
from .strava import Track

SNAPSHOT = Path(__file__).resolve().parent / "data" / "pistes.json"
RUN_M = 30  # how far from a run's line a GPS point can be and still be on it
MIN_ON_RUN_S = 10  # time on a run before it counts (crossing one doesn't)
MOVING_MPS = 2.0  # slower is standing around or in a line
MAX_STEP_S = 10  # GPS gaps count for at most this long
CELL_M = 60  # spatial index cell size


@dataclass(frozen=True)
class Piste:
    id: str  # OpenStreetMap way id
    name: str
    difficulty: str | None
    area: bool  # an outline (bowl, glade) rather than a line down the run
    points: tuple[tuple[float, float], ...]  # (lat, lon)


def piste_query(resort: str) -> str:
    lat, lon = RESORT_CENTERS[resort]
    box = f"{lat - RESORT_BOX},{lon - RESORT_BOX},{lat + RESORT_BOX},{lon + RESORT_BOX}"
    return f'[out:json][timeout:60];way["piste:type"="downhill"]({box});out geom tags;'


def parse_pistes(body: dict) -> list[Piste]:
    pistes = []
    for element in body.get("elements", []):
        geometry, tags = element.get("geometry") or [], element.get("tags") or {}
        if element.get("type") != "way" or len(geometry) < 2:
            continue
        points = tuple((round(p["lat"], 6), round(p["lon"], 6)) for p in geometry)
        closed = points[0] == points[-1] and len(points) > 3
        pistes.append(Piste(
            id=str(element["id"]),
            name=tags.get("piste:name") or tags.get("name") or "Unnamed run",
            difficulty=tags.get("piste:difficulty"),
            area=closed and tags.get("area") != "no",
            points=points,
        ))
    return pistes


def load_snapshot(file: Path = SNAPSHOT) -> dict[str, list[Piste]]:
    """resort -> runs, from the shipped snapshot."""
    if not file.exists():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    return {
        resort: [Piste(p["id"], p["name"], p["difficulty"], p["area"], tuple(map(tuple, p["points"]))) for p in pistes]
        for resort, pistes in data["resorts"].items()
    }


class PisteIndex:
    """Runs on a flat plane, bucketed into grid cells for quick nearest-run lookups."""

    def __init__(self, pistes: list[Piste], center: tuple[float, float]) -> None:
        self.project = Projector(*center)
        self.lines: dict[tuple[int, int], list[tuple[str, tuple, tuple]]] = defaultdict(list)
        self.areas: list[tuple[str, list[tuple[float, float]], tuple[float, float, float, float]]] = []
        for piste in pistes:
            xy = [self.project(la, lo) for la, lo in piste.points]
            if piste.area:
                xs, ys = [p[0] for p in xy], [p[1] for p in xy]
                self.areas.append((piste.id, xy, (min(xs), min(ys), max(xs), max(ys))))
                continue
            for a, b in zip(xy, xy[1:]):
                for cell in self._cells(a, b):
                    self.lines[cell].append((piste.id, a, b))

    @staticmethod
    def _cells(a, b):
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        for cx in range(math.floor((x0 - RUN_M) / CELL_M), math.floor((x1 + RUN_M) / CELL_M) + 1):
            for cy in range(math.floor((y0 - RUN_M) / CELL_M), math.floor((y1 + RUN_M) / CELL_M) + 1):
                yield cx, cy

    def run_at(self, lat: float, lon: float) -> str | None:
        x, y = self.project(lat, lon)
        best, best_d = None, RUN_M
        for piste_id, a, b in self.lines.get((math.floor(x / CELL_M), math.floor(y / CELL_M)), ()):
            d = segment_distance(x, y, a, b)
            if d <= best_d:
                best, best_d = piste_id, d
        if best:
            return best
        for piste_id, polygon, (x0, y0, x1, y1) in self.areas:
            if x0 <= x <= x1 and y0 <= y <= y1 and inside(x, y, polygon):
                return piste_id
        return None


def segment_distance(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / length2))
    return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))


def inside(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    result = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            result = not result
    return result


def speeds(track: Track, project: Projector) -> list[float]:
    if track.speed:
        return track.speed
    xy = [project(la, lo) for la, lo in zip(track.lat, track.lon)]
    result = [0.0]
    for i in range(1, len(xy)):
        dt = track.time[i] - track.time[i - 1]
        result.append(math.dist(xy[i], xy[i - 1]) / dt if dt > 0 else 0.0)
    return result


def runs_skied(track: Track, rides: list[Ride], index: PisteIndex) -> dict[str, int]:
    """Run id -> how many descents went down it, for the stretches between lift rides."""
    counts: dict[str, int] = defaultdict(int)
    speed = speeds(track, index.project)
    ordered = sorted(rides, key=lambda r: r.start)
    i = 0
    for ride, following in zip(ordered, ordered[1:] + [None]):
        start, end = ride.end, following.start if following else math.inf
        on_run: dict[str, float] = defaultdict(float)
        while i < len(track.time) and track.time[i] <= start:
            i += 1
        j = i
        while j < len(track.time) and track.time[j] < end:
            if speed[j] >= MOVING_MPS and j > 0:
                piste = index.run_at(track.lat[j], track.lon[j])
                if piste:
                    on_run[piste] += min(track.time[j] - track.time[j - 1], MAX_STEP_S)
            j += 1
        for piste, seconds in on_run.items():
            if seconds >= MIN_ON_RUN_S:
                counts[piste] += 1
    return dict(counts)


HEAT_CELL_M = 10  # heatmap grid cell size
MAX_JUMP_M = 300  # a longer gap between GPS points (signal lost) isn't drawn across
MAX_GAP_S = 60


def heat_cells(track: Track, center: tuple[float, float], box_m: float) -> list[list[int]]:
    """Where you went, as [x, y, passes] per HEAT_CELL_M grid cell (x east, y north, from the
    resort center), within box_m of it. The track is drawn cell by cell between GPS points, and
    a cell counts once each time you pass through it. Lift rides are included, as on Strava."""
    project = Projector(*center)
    passes: dict[tuple[int, int], int] = defaultdict(int)
    previous_cell, previous = None, None
    for t, lat, lon in zip(track.time, track.lat, track.lon):
        x, y = project(lat, lon)
        if abs(x) > box_m or abs(y) > box_m:
            previous_cell, previous = None, None
            continue
        if previous is None or t - previous[0] > MAX_GAP_S or math.dist((x, y), previous[1:]) > MAX_JUMP_M:
            points = [(x, y)]
        else:
            steps = max(1, int(math.dist((x, y), previous[1:]) / (HEAT_CELL_M / 2)))
            points = [(previous[1] + (x - previous[1]) * k / steps, previous[2] + (y - previous[2]) * k / steps)
                      for k in range(1, steps + 1)]
        for px, py in points:
            cell = (math.floor(px / HEAT_CELL_M), math.floor(py / HEAT_CELL_M))
            if cell != previous_cell:
                passes[cell] += 1
                previous_cell = cell
        previous = (t, x, y)
    return [[x, y, n] for (x, y), n in passes.items()]


def main() -> None:
    import argparse
    import asyncio

    import httpx

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", action="store_true", help=f"Fetch every resort's runs into {SNAPSHOT.name}")
    args = parser.parse_args()
    if not args.snapshot:
        parser.print_help()
        return

    async def run() -> None:
        resorts = {}
        async with httpx.AsyncClient(timeout=90, headers={"User-Agent": "davidcornes.com ski runs"}) as client:
            for resort in RESORT_CENTERS:
                for attempt in range(3):  # public Overpass servers are often busy
                    try:
                        pistes = parse_pistes(await overpass(client, piste_query(resort)))
                        break
                    except RuntimeError:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(30)
                resorts[resort] = [{"id": p.id, "name": p.name, "difficulty": p.difficulty, "area": p.area,
                                    "points": p.points} for p in pistes]
                print(resort, len(pistes), "runs")
        SNAPSHOT.write_text(json.dumps({"source": "OpenStreetMap contributors", "resorts": resorts},
                                       separators=(",", ":")), encoding="utf-8")

    asyncio.run(run())


if __name__ == "__main__":
    main()
