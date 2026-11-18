"""Vertical feet per minute of ride time for each operating lift at a few western ski areas.

Lift data comes from Lift Blog (liftblog.com), which publishes a Google Sheet per ski
area with every lift's status, vertical rise (feet) and ride time (minutes). Each sheet
is downloaded as CSV. Lifts the sheet leaves blank can be filled in from EXTRA_LIFTS.

run_lift_updates refreshes a LiftCache once a day (the website backend runs it in the
background and serves the result at /api/lifts). Run from the lifts/ directory to
print the current ranking:

    python -m lift_vertical.liftblog
"""

import asyncio
import csv
import io
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

SHEET_URL = "https://docs.google.com/spreadsheets/d/{sheet}/pub?gid={gid}&single=true&output=csv"
SOURCE = "https://liftblog.com"
REFRESH_SECONDS = 24 * 60 * 60  # lift specs rarely change
RETRY_SECONDS = 15 * 60

# Ski area -> (sheet id, tab id) of its published Lift Blog sheet.
RESORTS = {
    "Big Sky": ("1sVT6YWEAuxhdALGFf2v5VbBm6Li1XJidSnAVFYozQvk", 523354483),
    "Bridger Bowl": ("1MW3FrLkgTWrbAc65v52_TncsXhG3sK0rhXp2EyACUq4", 93846549),
    "Grand Targhee": ("14WJBgJxs4zPAKAiGe-2Cne0I2HdAMTjybwppM4j6VrE", 1178719126),
    "Jackson Hole": ("17BUxY09-noSsS6JQJtnd8z_7aF8rL_z9y_2lfYwSaMo", 550960280),
    "Revelstoke": ("1OAS27h3btXWCRNQo3CciD04ip23lftenF7aguC8r52s", 1986555129),
}

# Lifts (and hikes) that are missing or blank on a sheet: ski area -> name -> (type, vertical ft, ride min).
EXTRA_LIFTS = {
    "Bridger Bowl": {
        "Schlasman's": ("Double", 1427, 7.067),
        "Ridge Hike": ("Hike", 410, 13),
    },
}


@dataclass(frozen=True)
class Lift:
    resort: str
    name: str
    type: str
    vertical: float  # feet
    ride_time: float  # minutes

    @property
    def vert_per_minute(self) -> float:
        return self.vertical / self.ride_time

    def to_dict(self) -> dict:
        return {**asdict(self), "vert_per_minute": round(self.vert_per_minute, 1)}


def number(text: str) -> float | None:
    """'1,427' -> 1427.0; blank or non-numeric -> None."""
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None


def parse_sheet(resort: str, text: str) -> list[Lift]:
    """Operating lifts with a vertical rise and ride time, plus any EXTRA_LIFTS for the resort.

    The sheet's header row names the columns ("Status", "Lift Name", "Type", "Vertical
    Rise", "Ride Time", ...). Removed lifts are skipped; an extra replaces a sheet row
    with the same name (sheets often leave older lifts' numbers blank).
    """
    extras = EXTRA_LIFTS.get(resort, {})
    lifts = []
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("Lift Name") or "").strip()
        if row.get("Status", "").strip() != "Operating" or not name or name in extras:
            continue
        vertical, ride_time = number(row.get("Vertical Rise", "")), number(row.get("Ride Time", ""))
        if vertical and ride_time:
            lifts.append(Lift(resort, name, (row.get("Type") or "").strip(), vertical, ride_time))
    lifts += [Lift(resort, name, kind, vertical, minutes) for name, (kind, vertical, minutes) in extras.items()]
    return sorted(lifts, key=lambda lift: lift.vert_per_minute, reverse=True)


async def fetch_resort(client: httpx.AsyncClient, resort: str) -> list[Lift]:
    sheet, gid = RESORTS[resort]
    response = await client.get(SHEET_URL.format(sheet=sheet, gid=gid), follow_redirects=True)
    response.raise_for_status()
    return parse_sheet(resort, response.text)


async def fetch_lifts(client: httpx.AsyncClient) -> dict:
    """Every resort's operating lifts, fastest vertical first. A resort whose sheet fails is listed in `failed`."""
    results = await asyncio.gather(*(fetch_resort(client, r) for r in RESORTS), return_exceptions=True)
    lifts, failed = [], []
    for resort, result in zip(RESORTS, results):
        if isinstance(result, BaseException):
            log.warning("Couldn't fetch %s lifts: %s", resort, result)
            failed.append(resort)
        else:
            lifts += result
    if not lifts:
        raise RuntimeError("No lift data from any resort")
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": SOURCE,
        "resorts": [r for r in RESORTS if r not in failed],
        "failed": failed,
        "lifts": [lift.to_dict() for lift in lifts],
    }


class LiftCache:
    """The latest successful fetch (None until the first one)."""

    def __init__(self) -> None:
        self.latest: dict | None = None


async def run_lift_updates(target: LiftCache, refresh: float = REFRESH_SECONDS) -> None:
    """Fetch lift data now and then daily, until cancelled. Failures keep the last result."""
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            try:
                target.latest = await fetch_lifts(client)
                log.info("Fetched lift data (%d lifts)", len(target.latest["lifts"]))
                wait = refresh
            except Exception:
                log.exception("Couldn't fetch lift data; retrying in %ds", RETRY_SECONDS)
                wait = min(refresh, RETRY_SECONDS)
            await asyncio.sleep(wait)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()

    async def fetch() -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            return await fetch_lifts(client)

    data = asyncio.run(fetch())
    for resort in data["resorts"]:
        print(resort)
        for lift in (l for l in data["lifts"] if l["resort"] == resort):
            print(f"  {lift['name']:<28} {lift['vertical']:>6.0f} ft {lift['ride_time']:>5.1f} min {lift['vert_per_minute']:>6.1f} ft/min")
    for resort in data["failed"]:
        print(f"{resort}: couldn't fetch")


if __name__ == "__main__":
    main()
