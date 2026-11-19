"""Analysed ski days, and keeping them in Postgres so they survive deploys (Render's disk is
wiped on each one).

Set DATABASE_URL (e.g. a Neon connection string) to use it. Each row is one Strava activity:
the resort, the lift rides and laps found in it, the runs skied (OpenStreetMap way id ->
descents), where you went as a 10 m grid of cells around the resort (no raw GPS points or
times), and the date. Dates and single days stay in the database; the site only publishes
totals over many days.
"""

import json
import os
from dataclasses import asdict, dataclass

from .laps import Lap, Ride


@dataclass
class SkiDay:
    resort: str | None
    rides: list[Ride]
    laps: list[Lap]
    date: str | None = None  # local date, "2025-01-31"
    runs: dict[str, int] | None = None  # OSM piste way id -> descents on it; None until worked out
    heat: list[list[int]] | None = None  # [x, y, passes] per 10 m grid cell around the resort; None until worked out

    def to_json(self) -> dict:
        return {"resort": self.resort, "rides": [asdict(r) for r in self.rides], "laps": [asdict(lap) for lap in self.laps],
                "date": self.date, "runs": self.runs, "heat": self.heat}

    @classmethod
    def from_json(cls, data: dict) -> "SkiDay":
        return cls(data["resort"], [Ride(**r) for r in data["rides"]], [Lap(**lap) for lap in data["laps"]],
                   data.get("date"), data.get("runs"), data.get("heat"))

    def richness(self) -> int:
        """How much is known, to keep the fuller copy when two disagree."""
        return (self.date is not None) + (self.runs is not None) + (self.heat is not None)


SCHEMA = """
CREATE TABLE IF NOT EXISTS ski_days (
    activity_id bigint PRIMARY KEY,
    resort text,
    rides jsonb NOT NULL,
    laps jsonb NOT NULL,
    analysed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE ski_days ADD COLUMN IF NOT EXISTS start_date date;
ALTER TABLE ski_days ADD COLUMN IF NOT EXISTS runs jsonb;
ALTER TABLE ski_days ADD COLUMN IF NOT EXISTS heat jsonb;
CREATE TABLE IF NOT EXISTS snow_days (
    resort text NOT NULL,
    day date NOT NULL,
    window_hours int NOT NULL,
    new_snow_in real,
    station text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (resort, day, window_hours)
);
"""

SNOW_UPSERT = """
INSERT INTO snow_days (resort, day, window_hours, new_snow_in, station) VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (resort, day, window_hours) DO UPDATE SET new_snow_in = EXCLUDED.new_snow_in,
    station = EXCLUDED.station, fetched_at = now()
"""

UPSERT = """
INSERT INTO ski_days (activity_id, resort, rides, laps, start_date, runs, heat) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (activity_id) DO UPDATE SET resort = EXCLUDED.resort, rides = EXCLUDED.rides, laps = EXCLUDED.laps,
    start_date = COALESCE(EXCLUDED.start_date, ski_days.start_date), runs = COALESCE(EXCLUDED.runs, ski_days.runs),
    heat = COALESCE(EXCLUDED.heat, ski_days.heat),
    analysed_at = now()
"""


class PostgresDays:
    """Loads and saves analysed days. Calls block; run them with asyncio.to_thread."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._ready = False

    @classmethod
    def from_env(cls) -> "PostgresDays | None":
        url = os.environ.get("DATABASE_URL", "").strip()
        return cls(url) if url else None

    def _connect(self):
        import psycopg  # only needed when a database is configured

        connection = psycopg.connect(self.url, connect_timeout=15)
        if not self._ready:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA)
            connection.commit()
            self._ready = True
        return connection

    def load(self) -> dict[int, SkiDay]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT activity_id, resort, rides, laps, start_date, runs, heat FROM ski_days")
            return {
                activity_id: SkiDay(resort, [Ride(**r) for r in rides], [Lap(**lap) for lap in laps],
                                    start_date.isoformat() if start_date else None, runs, heat)
                for activity_id, resort, rides, laps, start_date, runs, heat in cursor.fetchall()
            }

    def save(self, days: dict[int, SkiDay]) -> None:
        """Insert or replace these days (a missing date or runs never erases a stored one)."""
        if not days:
            return
        rows = [
            (activity_id, day.resort, json.dumps([asdict(r) for r in day.rides]), json.dumps([asdict(lap) for lap in day.laps]),
             day.date, None if day.runs is None else json.dumps(day.runs),
             None if day.heat is None else json.dumps(day.heat, separators=(",", ":")))
            for activity_id, day in days.items()
        ]
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(UPSERT, rows)

    def load_snow(self, window_hours: int) -> dict[tuple[str, str], float | None]:
        """New snow per (resort, date) measured over window_hours (None: no reading that day)."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT resort, day, new_snow_in FROM snow_days WHERE window_hours = %s", (window_hours,))
            return {(resort, day.isoformat()): inches for resort, day, inches in cursor.fetchall()}

    def save_snow(self, window_hours: int, snow: dict[tuple[str, str], tuple[float | None, str]]) -> None:
        """Save new snow per (resort, date), as (inches, the SNOTEL station it came from)."""
        if not snow:
            return
        rows = [(resort, day, window_hours, inches, station) for (resort, day), (inches, station) in snow.items()]
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(SNOW_UPSERT, rows)
