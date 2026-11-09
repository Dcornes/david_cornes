"""Live market quotes for the website, streamed to browsers with Server-Sent Events.

MarketDataHandler (from collector/market_data) runs in the background and calls
QuoteBroadcaster.publish with a snapshot whenever a price changes. Browsers
connected to /api/quotes/stream get those snapshots, at most a few per second per pair.
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
from market_data.market_data_handler import MarketDataHandler, MarketSnapshot

log = logging.getLogger(__name__)

DEFAULT_SYMBOLS = "BTC-USDT,ETH-USDT,SOL-USDT"
UPDATES_PER_SECOND = 4  # per pair; faster changes are combined, always ending on the newest price
HEARTBEAT_SECONDS = 15  # keeps idle connections open through proxies
QUEUE_SIZE = 200  # per browser; if a slow browser falls behind, its oldest updates are dropped


def sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


class QuoteBroadcaster:
    """Keeps the latest snapshot per pair and sends them to every subscriber.

    Each pair is sent at most once per min_interval seconds. A snapshot that arrives
    sooner isn't lost: the newest one is sent as soon as the interval is up. Each
    message is serialized once and the same string goes to every subscriber.
    """

    def __init__(self, min_interval: float = 1 / UPDATES_PER_SECOND) -> None:
        self.min_interval = min_interval
        self.latest: dict[str, dict] = {}  # symbol -> newest snapshot (sent or not yet)
        self._last_sent: dict[str, float] = {}  # symbol -> time.monotonic() of last send
        self._scheduled: dict[str, asyncio.TimerHandle] = {}  # symbol -> pending delayed send
        self._subscribers: set[asyncio.Queue[str]] = set()

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
        message = sse(self.latest[symbol])  # once, shared by every subscriber
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()  # drop the oldest; newer prices matter more
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
    """Send the current snapshot of every pair, then each new one as prices change."""
    queue = source.subscribe()
    try:
        for snapshot in list(source.latest.values()):
            yield sse(snapshot)
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
    """Stream OKX, Kraken and Binance.US quotes into the broadcaster until cancelled."""

    def publish(snapshot: MarketSnapshot) -> None:
        source.publish(snapshot.to_dict())

    log.info("Starting market data for %s", ", ".join(symbols))
    await MarketDataHandler(symbols, on_update=publish, reconnect=True).run()


def configured_symbols() -> list[str]:
    """Pairs to stream, from QUOTE_SYMBOLS (comma-separated) or the defaults."""
    return [s.strip() for s in os.environ.get("QUOTE_SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]


router = APIRouter(prefix="/api/quotes", tags=["quotes"])


@router.get("")
def latest_quotes() -> list[dict]:
    """Current snapshot for every pair."""
    return list(broadcaster.latest.values())


@router.get("/stream")
async def stream_quotes() -> StreamingResponse:
    """Server-Sent Events: a `data:` message with a pair's snapshot when its prices change (max 4/s per pair)."""
    return StreamingResponse(
        event_stream(broadcaster),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
