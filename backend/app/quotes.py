"""Live market quotes for the website, streamed to browsers with Server-Sent Events.

MarketDataHandler (from collector/market_data) runs in the background and calls
QuoteBroadcaster.publish with a snapshot whenever a price changes. Browsers
connected to /api/quotes/stream get a full snapshot of each pair, then only what
changed (an `update` event), at most once a second per pair. A `status` event
carries every exchange feed's health whenever one changes.
"""

import asyncio
import json
import logging
import math
import os
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from market_data.market_data_handler import FeedStatus, MarketDataHandler, MarketSnapshot

log = logging.getLogger(__name__)

DEFAULT_SYMBOLS = "BTC-USDT,ETH-USDT"
UPDATES_PER_SECOND = 1  # per pair; faster changes are combined, always ending on the newest price
HEARTBEAT_SECONDS = 15  # keeps idle connections open through proxies
QUEUE_SIZE = 200  # per browser; a browser that falls this far behind is sent full snapshots instead


def sse(data: dict | list, event: str | None = None) -> str:
    """One Server-Sent Events message. Unnamed ones arrive in the browser's onmessage."""
    name = f"event: {event}\n" if event else ""
    return f"{name}data: {json.dumps(data)}\n\n"


PRICE_FIELDS = ("best_bid", "best_ask", "spread", "crossed")


def price_changed(old: dict | None, new: dict) -> bool:
    return old is None or (old["bid"], old["ask"]) != (new["bid"], new["ask"])


def snapshot_update(before: dict, after: dict) -> dict:
    """What changed in a pair between two snapshots: the exchanges whose bid or ask price
    changed (sent whole, with current quantities), the exchanges that dropped out, and the
    overall best bid/ask (always included; it's small). Quantity-only changes aren't sent."""
    old = {q["exchange"]: q for q in before["exchanges"]}
    new = {q["exchange"]: q for q in after["exchanges"]}
    return {
        "symbol": after["symbol"],
        "changed": [q for name, q in new.items() if price_changed(old.get(name), q)],
        "removed": [name for name in old if name not in new],
        **{field: after[field] for field in PRICE_FIELDS},
    }


def apply_update(before: dict, update: dict) -> dict:
    """The snapshot a browser has after applying an update (same merge as the frontend)."""
    quotes = {q["exchange"]: q for q in before["exchanges"]}
    for name in update["removed"]:
        del quotes[name]
    for quote in update["changed"]:
        quotes[quote["exchange"]] = quote
    return {
        "symbol": update["symbol"],
        "exchanges": [quotes[name] for name in sorted(quotes)],
        **{field: update[field] for field in PRICE_FIELDS},
    }


def is_empty(update: dict, before: dict) -> bool:
    """Nothing a browser would see changes (e.g. a price moved and moved back within the interval)."""
    return not update["changed"] and not update["removed"] and all(update[f] == before[f] for f in PRICE_FIELDS)


class QuoteBroadcaster:
    """Keeps the latest snapshot per pair and sends changes to every subscriber.

    Each pair is sent at most once per min_interval seconds. A snapshot that arrives
    sooner isn't lost: the newest one is sent as soon as the interval is up. The first
    send of a pair is the full snapshot; after that only the exchanges whose prices
    changed since the last send. Each message is serialized once and the same string
    goes to every subscriber.
    """

    def __init__(self, min_interval: float = 1 / UPDATES_PER_SECOND) -> None:
        self.min_interval = min_interval
        self.latest: dict[str, dict] = {}  # symbol -> newest snapshot (sent or not yet)
        # symbol -> what browsers have: current prices, but quantities and times only as of
        # each exchange's last price change. New browsers start from this.
        self.sent: dict[str, dict] = {}
        self._last_sent: dict[str, float] = {}  # symbol -> time.monotonic() of last send
        self._scheduled: dict[str, asyncio.TimerHandle] = {}  # symbol -> pending delayed send
        self._subscribers: set[asyncio.Queue[str]] = set()
        self.feeds: list[dict] = []  # latest health of each exchange feed

    def publish_status(self, feeds: list[dict]) -> None:
        """Send every exchange feed's health. Rare (only on changes), so not rate limited."""
        self.feeds = feeds
        self._broadcast(sse(feeds, event="status"))

    def publish(self, snapshot: dict) -> None:
        symbol = snapshot["symbol"]
        self.latest[symbol] = snapshot
        if symbol in self._scheduled:
            return  # a send is already booked; it will use this newer snapshot
        wait = self._last_sent.get(symbol, -math.inf) + self.min_interval - time.monotonic()
        if wait <= 0:
            self._send(symbol)
        else:
            self._scheduled[symbol] = asyncio.get_running_loop().call_later(wait, self._send, symbol)

    def _send(self, symbol: str) -> None:
        self._scheduled.pop(symbol, None)
        self._last_sent[symbol] = time.monotonic()
        before, after = self.sent.get(symbol), self.latest[symbol]
        if before is None:
            self.sent[symbol] = after
            self._broadcast(sse(after))  # serialized once, shared by every subscriber
            return
        update = snapshot_update(before, after)
        if is_empty(update, before):
            return
        self.sent[symbol] = apply_update(before, update)
        self._broadcast(sse(update, event="update"))

    def _broadcast(self, message: str) -> None:
        for queue in self._subscribers:
            if queue.full():
                self._resync(queue)  # message is already included in the fresh snapshots
            else:
                queue.put_nowait(message)

    def initial_messages(self) -> list[str]:
        """Everything a browser needs to start from: feed health and a full snapshot of every pair."""
        status = [sse(self.feeds, event="status")] if self.feeds else []
        return status + [sse(snapshot) for snapshot in self.sent.values()]

    def _resync(self, queue: asyncio.Queue[str]) -> None:
        """A browser fell far behind. Updates only make sense in order, so instead of dropping
        some, replace its whole backlog with full snapshots of the current state."""
        while not queue.empty():
            queue.get_nowait()
        for message in self.initial_messages():
            queue.put_nowait(message)

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


broadcaster = QuoteBroadcaster()


async def event_stream(source: QuoteBroadcaster, heartbeat: float = HEARTBEAT_SECONDS) -> AsyncIterator[str]:
    """Send feed health and a full snapshot of every pair, then each change as it happens.

    The snapshots are as of the last send (not the newest), because the updates that
    follow are relative to those.
    """
    queue = source.subscribe()
    try:
        for message in source.initial_messages():
            yield message
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                yield ": heartbeat\n\n"  # SSE comment; browsers ignore it
                continue
            yield message
    finally:
        source.unsubscribe(queue)  # browser disconnected


async def run_market_data(symbols: list[str], source: QuoteBroadcaster = broadcaster) -> None:
    """Stream quotes from every exchange into the broadcaster until cancelled."""

    def publish(snapshot: MarketSnapshot) -> None:
        source.publish(snapshot.to_dict())

    def publish_status(statuses: list[FeedStatus]) -> None:
        source.publish_status([s.to_dict() for s in statuses])

    log.info("Starting market data for %s", ", ".join(symbols))
    handler = MarketDataHandler(symbols, on_update=publish, reconnect=True, on_status=publish_status)
    publish_status(handler.statuses())  # every feed starts as "connecting"
    await handler.run()


def configured_symbols() -> list[str]:
    """Pairs to stream, from QUOTE_SYMBOLS (comma-separated) or the defaults."""
    return [s.strip() for s in os.environ.get("QUOTE_SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]


router = APIRouter(prefix="/api/quotes", tags=["quotes"])


@router.get("")
def latest_quotes() -> list[dict]:
    """Current snapshot for every pair."""
    return list(broadcaster.latest.values())


@router.get("/status")
def feed_status() -> list[dict]:
    """Health of each exchange feed: connecting, live, reconnecting or stopped."""
    return broadcaster.feeds


@router.get("/stream")
async def stream_quotes() -> StreamingResponse:
    """Server-Sent Events: a full snapshot of each pair, then an `update` event with what
    changed (max 1/s per pair), and a `status` event with every feed's health when one changes."""
    return StreamingResponse(
        event_stream(broadcaster),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
