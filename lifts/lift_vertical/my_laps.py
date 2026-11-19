"""Your real lift rides and laps, from Strava, summarized per lift.

run_my_laps_updates checks Strava every few hours for new ski days, finds the lift rides
and laps in each (see laps.py), and keeps per-lift medians in a MyLapsCache. Each day's
result is kept, so only new days are fetched. Only totals and medians are published,
never GPS tracks or dates. Run from the lifts/ directory (with the Strava variables
set, e.g. from backend/.env) to print your numbers:

    python -m lift_vertical.my_laps
"""

import asyncio
import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .day_store import PostgresDays, SkiDay
from .runs import PisteIndex, heat_cells, load_snapshot, runs_skied
from .aerialways import RESORT_BOX, RESORT_CENTERS, AerialwayCache, name_key, resort_near
from .laps import FEET_PER_METER, Lap, Ride, find_laps, find_rides, summarize_lift
from .strava import Credentials, Strava, StravaError

log = logging.getLogger(__name__)

MAX_DAYS: int | None = None  # every ski day on Strava
# Saved days at these resorts get their runs worked out (one more GPS download each, then kept).
BACKFILL_RUNS_RESORTS = ("Big Sky", "Bridger Bowl", "Jackson Hole", "Grand Targhee", "Revelstoke")
STRAVA_PACE_SECONDS = 9.5  # between GPS downloads: Strava allows 100 requests per 15 minutes
REFRESH_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 30 * 60
# Analysed days (lift names and timings only), shared by the backend and this script locally.
DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "backend" / ".cache" / "my_laps.json"


class MyLapsCache:
    """Analysed ski days and the latest summary. With a file, days are saved after each one
    and loaded at start, so a restart doesn't analyse everything again. The file holds only
    lift names and timings per Strava activity id (no GPS, no dates)."""

    def __init__(self, file: Path | None = None, store: PostgresDays | None = None) -> None:
        self.days: dict[int, SkiDay] = {}  # activity id -> what was found that day
        self.latest: dict | None = None
        self.error: str | None = None
        self.file = file
        self.store = store  # Postgres, when DATABASE_URL is set: keeps days across deploys
        self.synced = False
        if file and file.exists():
            try:
                saved = json.loads(file.read_text(encoding="utf-8"))
                self.days = {int(key): SkiDay.from_json(day) for key, day in saved.items()}
                log.info("Loaded %d analysed ski days from %s", len(self.days), file)
            except (ValueError, KeyError, TypeError) as error:
                log.warning("Ignoring an unreadable lap cache (%s); days will be analysed again", error)

    def save(self) -> None:
        if not self.file:
            return
        data = {str(key): day.to_json() for key, day in self.days.items()}
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(data), encoding="utf-8")

    async def sync_store(self) -> None:
        """Merge the database's days with these, and upload any it's missing (once per start)."""
        if not self.store or self.synced:
            return
        try:
            stored = await asyncio.to_thread(self.store.load)
            # Upload days the database lacks, or knows less about (no date or runs yet).
            missing = {key: day for key, day in self.days.items()
                       if key not in stored or day.richness() > stored[key].richness()}
            await asyncio.to_thread(self.store.save, missing)
        except Exception as error:  # noqa: BLE001 - the database is optional; keep going without it
            log.warning("Couldn't reach the lap database (%s); trying again next check", error)
            return
        added = len(stored.keys() - self.days.keys())
        for key, day in stored.items():
            if key not in self.days or day.richness() >= self.days[key].richness():
                self.days[key] = day
        self.synced = True
        log.info("Lap database: %d days loaded, %d new here, %d uploaded", len(stored), added, len(missing))
        if added:
            self.save()

    async def record(self, *activity_ids: int) -> None:
        """Save newly analysed or updated days to the file and the database."""
        self.save()
        if self.store and self.synced and activity_ids:
            try:
                await asyncio.to_thread(self.store.save, {key: self.days[key] for key in activity_ids})
            except Exception as error:  # noqa: BLE001
                log.warning("Couldn't save a day to the lap database (%s); it's uploaded on the next start", error)
                self.synced = False  # the next check syncs again, uploading what's missing


def summarize(days: dict[int, SkiDay], liftblog_names: dict[str, dict[str, str]]) -> dict:
    """Per-lift medians across every day, plus your overall ski-down speed.

    liftblog_names: resort -> name_key -> Lift Blog's name, to line lifts up with the main table.
    """
    rides_by: dict[tuple, list[Ride]] = defaultdict(list)
    laps_by: dict[tuple, list[Lap]] = defaultdict(list)
    for day in days.values():
        for ride in day.rides:
            rides_by[(day.resort, ride.lift)].append(ride)
        for lap in day.laps:
            laps_by[(day.resort, lap.lift)].append(lap)

    lifts = []
    for (resort, lift), rides in rides_by.items():
        entry = summarize_lift(rides, laps_by[(resort, lift)])
        names = liftblog_names.get(resort or "", {})
        # "Powder Seeker 6" (OSM, with the seat count) is Lift Blog's "Powder Seeker".
        matched = names.get(name_key(lift)) or names.get(name_key(lift).rstrip("0123456789"))
        lifts.append({"resort": resort or "Other", "lift": lift, "liftblog_name": matched, **entry})
    lifts.sort(key=lambda l: (-(l["lap_vert_per_minute"] or 0), -l["rides"]))

    all_laps = [lap for day in days.values() for lap in day.laps if lap.ski_s > 0]
    rate = statistics.median(lap.descent_m * FEET_PER_METER / (lap.ski_s / 60) for lap in all_laps) if all_laps else None
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "days": sum(1 for day in days.values() if day.rides),
        "rides": sum(len(r) for r in rides_by.values()),
        "laps": len(all_laps),
        "ski_rate_ft_min": rate,
        "lifts": lifts,
    }


_indexes: dict[str, PisteIndex] = {}


def piste_index(resort: str | None) -> PisteIndex | None:
    """The resort's runs (from the shipped snapshot), ready for matching; None if not mapped."""
    if resort and resort not in _indexes:
        pistes = load_snapshot().get(resort)
        if pistes:
            _indexes[resort] = PisteIndex(pistes, RESORT_CENTERS[resort])
    return _indexes.get(resort or "")


def needs_runs(day: SkiDay) -> bool:
    """A saved day whose runs or heatmap cells aren't worked out yet, at a resort where they're wanted."""
    return (day.runs is None or day.heat is None) and bool(day.rides) and day.resort in BACKFILL_RUNS_RESORTS


def resort_heat(track, resort: str | None) -> list[list[int]] | None:
    """The day's heatmap cells around its resort (None away from a known resort)."""
    if resort not in RESORT_CENTERS:
        return None
    return heat_cells(track, RESORT_CENTERS[resort], RESORT_BOX * 110_540)


async def update(cache: MyLapsCache, strava: Strava, client: httpx.AsyncClient, aerialways: AerialwayCache,
                 liftblog_names: dict[str, dict[str, str]]) -> int:
    """Analyse any new ski days, and fill in dates and runs for saved ones that lack them.
    Returns how many days were new."""
    await cache.sync_store()
    before = len(cache.days)
    if cache.days and cache.latest is None:
        cache.latest = summarize(cache.days, liftblog_names)  # saved days, while new ones are checked
    activities = await strava.ski_activities(MAX_DAYS)

    # Dates come with the activity list, so saved days get theirs without more requests.
    dated = []
    for activity in activities:
        day = cache.days.get(activity["id"])
        if day and day.date is None and activity.get("start_date_local"):
            day.date = activity["start_date_local"][:10]
            dated.append(activity["id"])
    if dated:
        await cache.record(*dated)
        log.info("Added dates to %d saved ski days", len(dated))

    # New days first, then saved days that still need their runs (one GPS download each).
    todo = [a for a in activities if a["id"] not in cache.days]
    todo += sorted((a for a in activities if a["id"] in cache.days and needs_runs(cache.days[a["id"]])),
                   key=lambda a: list(BACKFILL_RUNS_RESORTS).index(cache.days[a["id"]].resort))
    for activity in todo:
        key, saved = activity["id"], cache.days.get(activity["id"])
        track = await strava.track(key)
        if track is None:
            if saved:
                saved.runs, saved.heat = {}, []
            else:
                cache.days[key] = SkiDay(None, [], [], activity.get("start_date_local", "")[:10] or None, {}, [])
            await cache.record(key)
            await asyncio.sleep(STRAVA_PACE_SECONDS)
            continue
        if saved:  # already analysed: just the runs
            index = piste_index(saved.resort)
            saved.runs = runs_skied(track, saved.rides, index) if index else {}
            saved.heat = resort_heat(track, saved.resort) or []
            log.info("Runs for a saved ski day at %s: %d runs, %d heat cells (%d left to do)", saved.resort,
                     len(saved.runs), len(saved.heat),
                     sum(needs_runs(d) for d in cache.days.values()))
        else:
            try:
                lifts = await aerialways.around(client, track.lat, track.lon)
            except RuntimeError as error:
                log.warning("Skipping a ski day for now: %s", error)  # not cached, so it's retried next time
                continue
            rides = find_rides(track, lifts)
            resort = resort_near(statistics.median(track.lat), statistics.median(track.lon))
            laps = find_laps(track, rides)
            index = piste_index(resort)
            runs = runs_skied(track, rides, index) if index else None
            cache.days[key] = SkiDay(resort, rides, laps, activity.get("start_date_local", "")[:10] or None, runs,
                                     resort_heat(track, resort))
            log.info("Ski day %s at %s: %d GPS points, %d lift rides, %d laps, %d runs", len(cache.days),
                     resort or "an unlisted area", len(track.time), len(rides), len(laps), len(runs or {}))
            cache.latest = summarize(cache.days, liftblog_names)  # show progress as days are added
        await cache.record(key)
        await asyncio.sleep(STRAVA_PACE_SECONDS)  # stay under Strava's rate limit (and be gentle with Overpass)
    cache.latest = summarize(cache.days, liftblog_names)
    cache.error = None
    return len(cache.days) - before


def liftblog_index(lift_data: dict | None) -> dict[str, dict[str, str]]:
    names: dict[str, dict[str, str]] = defaultdict(dict)
    for lift in (lift_data or {}).get("lifts", []):
        key = name_key(lift["name"])
        names[lift["resort"]][key] = lift["name"]
        names[lift["resort"]].setdefault(key.rstrip("0123456789"), lift["name"])
    return names


async def run_my_laps_updates(cache: MyLapsCache, lift_data=lambda: None, refresh: float = REFRESH_SECONDS,
                              on_new_days=lambda count: None) -> None:
    """Check Strava now and then every few hours, until cancelled. Does nothing without credentials.
    on_new_days(count) is called after a check that found new ski days (to refresh the lift maps)."""
    credentials = Credentials.from_env()
    if credentials is None:
        log.info("Strava isn't configured; skipping your lap times")
        return
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "davidcornes.com lift laps"}) as client:
        strava, aerialways = Strava(client, credentials), AerialwayCache()
        first_check = True
        while True:
            try:
                new_days = await update(cache, strava, client, aerialways, liftblog_index(lift_data()))
                log.info("Updated lap times from %d Strava ski days (%d new)", cache.latest["days"], new_days)
                if new_days and not first_check:
                    on_new_days(new_days)  # the first check after a restart just catches up
                first_check = False
                wait = refresh
            except (StravaError, httpx.HTTPError) as error:
                cache.error = str(error)
                log.warning("Couldn't update lap times from Strava: %s", error)
                wait = min(refresh, RETRY_SECONDS)
            await asyncio.sleep(wait)


def main() -> None:
    import argparse

    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[2] / "backend" / ".env")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="Where analysed days are kept between runs")
    args = parser.parse_args()
    load_dotenv(args.env)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    credentials = Credentials.from_env()
    if credentials is None:
        raise SystemExit("Set the STRAVA_ variables first (see backend/.env).")

    async def run() -> dict:
        from .liftblog import fetch_lifts

        cache = MyLapsCache(args.cache, PostgresDays.from_env())
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "davidcornes.com lift laps"}) as client:
            lift_data = await fetch_lifts(client)
            await update(cache, Strava(client, credentials), client, AerialwayCache(), liftblog_index(lift_data))
        return cache.latest

    data = asyncio.run(run())
    rate = data["ski_rate_ft_min"]
    print(f"{data['days']} ski days, {data['rides']} lift rides, {data['laps']} laps; "
          f"ski-down speed {rate:.0f} ft/min" if rate else f"{data['days']} ski days, no laps found")
    for lift in data["lifts"]:
        trip = f"{lift['round_trip_minutes']:.1f} min round trip" if lift["round_trip_minutes"] else "no laps"
        print(f"  {lift['resort']}: {lift['lift']} - {lift['rides']} rides, {lift['ride_minutes']:.1f} min ride, {trip}")


if __name__ == "__main__":
    main()
