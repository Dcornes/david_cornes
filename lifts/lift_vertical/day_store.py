"""Analysed ski days in Postgres, so they survive deploys (Render's disk is wiped on each one).

Set DATABASE_URL (e.g. a Neon connection string) to use it. Each row is one Strava activity:
the resort and the lift rides and laps found in it, as JSON. No GPS points and no dates.
"""

import json
import os
from dataclasses import asdict

from .laps import Lap, Ride

Day = tuple[str | None, list[Ride], list[Lap]]  # (resort, rides, laps)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ski_days (
    activity_id bigint PRIMARY KEY,
    resort text,
    rides jsonb NOT NULL,
    laps jsonb NOT NULL,
    analysed_at timestamptz NOT NULL DEFAULT now()
)
"""

UPSERT = """
INSERT INTO ski_days (activity_id, resort, rides, laps) VALUES (%s, %s, %s, %s)
ON CONFLICT (activity_id) DO UPDATE SET resort = EXCLUDED.resort, rides = EXCLUDED.rides,
    laps = EXCLUDED.laps, analysed_at = now()
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

    def load(self) -> dict[int, Day]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT activity_id, resort, rides, laps FROM ski_days")
            return {
                activity_id: (resort, [Ride(**r) for r in rides], [Lap(**lap) for lap in laps])
                for activity_id, resort, rides, laps in cursor.fetchall()
            }

    def save(self, days: dict[int, Day]) -> None:
        """Insert or replace these days."""
        if not days:
            return
        rows = [
            (activity_id, resort, json.dumps([asdict(r) for r in rides]), json.dumps([asdict(lap) for lap in laps]))
            for activity_id, (resort, rides, laps) in days.items()
        ]
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(UPSERT, rows)
