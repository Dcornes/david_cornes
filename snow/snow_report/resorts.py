"""Snow totals and webcams for every resort, refreshed every hour.

run_snow_updates fetches each resort into a SnowCache (the website backend runs it in
the background and serves the result at /api/snow). A resort whose source fails keeps
its last good result, marked with when that was. Run from the snow/ directory to print
the current numbers:

    python -m snow_report.resorts
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx

from . import big_sky, bridger_bowl, grand_targhee, jackson_hole, palisades_tahoe, revelstoke

log = logging.getLogger(__name__)

# In display order.
RESORTS = [bridger_bowl, big_sky, jackson_hole, grand_targhee, revelstoke, palisades_tahoe]
REFRESH_SECONDS = 60 * 60
RETRY_SECONDS = 5 * 60  # after a fetch where every resort failed


class SnowCache:
    """The latest result for each resort (empty until the first fetch)."""

    def __init__(self) -> None:
        self.resorts: dict[str, dict] = {}  # resort id -> Resort.to_dict() plus "fetched_at"
        self.fetched_at: str | None = None

    @property
    def latest(self) -> dict | None:
        if not self.resorts:
            return None
        order = [m.ID for m in RESORTS]
        return {
            "fetched_at": self.fetched_at,
            "resorts": sorted(self.resorts.values(), key=lambda r: order.index(r["id"]) if r["id"] in order else len(order)),
        }


async def update(cache: SnowCache, client: httpx.AsyncClient, resorts=RESORTS) -> int:
    """Fetch every resort into the cache. Returns how many succeeded."""
    now = datetime.now(timezone.utc).isoformat()
    results = await asyncio.gather(*(module.fetch(client) for module in resorts), return_exceptions=True)
    succeeded = 0
    for module, result in zip(resorts, results):
        if isinstance(result, BaseException):
            log.warning("Couldn't fetch %s: %r", module.NAME, result)
            if module.ID in cache.resorts:
                cache.resorts[module.ID]["stale"] = True
            continue
        cache.resorts[module.ID] = {**result.to_dict(), "fetched_at": now, "stale": False}
        succeeded += 1
    cache.fetched_at = now
    return succeeded


async def run_snow_updates(cache: SnowCache, refresh: float = REFRESH_SECONDS) -> None:
    """Fetch every resort now and then hourly, until cancelled."""
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "davidcornes.com snow page"}) as client:
        while True:
            try:
                succeeded = await update(cache, client)
                log.info("Fetched snow totals for %d of %d resorts", succeeded, len(RESORTS))
                wait = refresh if succeeded else min(refresh, RETRY_SECONDS)
            except Exception:
                log.exception("Snow update failed; retrying in %ds", RETRY_SECONDS)
                wait = min(refresh, RETRY_SECONDS)
            await asyncio.sleep(wait)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Print everything as JSON")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    async def fetch() -> SnowCache:
        cache = SnowCache()
        async with httpx.AsyncClient(timeout=20) as client:
            await update(cache, client)
        return cache

    data = asyncio.run(fetch()).latest or {"resorts": []}
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    for resort in data["resorts"]:
        stats = ", ".join(f"{s['label']} {s['value']}" + (f" ({s['detail']})" if s["detail"] else "") for s in resort["summary"])
        print(f"{resort['name']}: {stats}; {len(resort['webcams'])} webcams")


if __name__ == "__main__":
    main()
