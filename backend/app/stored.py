"""Cached data that's fetched from other sites only when it's stale and someone asks for it.

A StoredValue lives in memory and, when DATABASE_URL is set, in the cache_entries table, so
restarts and deploys don't fetch it again. get() returns what's cached; if it's older than
max_age it starts one refresh in the background (or waits for it, when there's nothing yet).
Nothing is fetched while nobody visits.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from lift_vertical.day_store import PostgresDays

log = logging.getLogger(__name__)

FIRST_FETCH_WAIT = 30  # seconds a first visitor waits for data before getting "try again"
RETRY_SECONDS = 5 * 60  # after a failed refresh


class StoredValue:
    def __init__(self, key: str, max_age: float, fetch: Callable[[dict | None], Awaitable[dict | None]]) -> None:
        """fetch(previous) returns fresh data (it may merge in the previous data), or None if it failed."""
        self.key = key
        self.max_age = max_age
        self.fetch = fetch
        self.data: dict | None = None
        self.saved_at = 0.0  # Unix time of the data
        self.enabled = True  # False: never fetch (tests, or a fetcher turned off)
        self._loaded = False
        self._last_try = 0.0
        self._task: asyncio.Task | None = None

    def store(self) -> PostgresDays | None:
        return PostgresDays.from_env()

    async def get(self) -> dict | None:
        if not self._loaded:
            self._loaded = True
            await self._load()
        stale = time.time() - self.saved_at > self.max_age
        if self.enabled and stale and self._task is None and time.time() - self._last_try > RETRY_SECONDS:
            self._task = asyncio.create_task(self._refresh())
        if self.data is None and self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), FIRST_FETCH_WAIT)
            except TimeoutError:
                pass
        return self.data

    async def _load(self) -> None:
        store = self.store()
        if store is None or self.data is not None:
            return
        try:
            entry = await asyncio.to_thread(store.load_entry, self.key)
        except Exception as error:  # noqa: BLE001 - the database is optional
            log.warning("Couldn't load cached %s (%s)", self.key, error)
            return
        if entry:
            self.data, self.saved_at = entry
            log.info("Loaded cached %s from the database", self.key)

    async def _refresh(self) -> None:
        self._last_try = time.time()
        try:
            fresh = await self.fetch(self.data)
            if fresh is not None:
                self.data, self.saved_at = fresh, time.time()
                store = self.store()
                if store is not None:
                    await asyncio.to_thread(store.save_entry, self.key, fresh)
        except Exception:
            log.exception("Couldn't refresh %s", self.key)
        finally:
            self._task = None
