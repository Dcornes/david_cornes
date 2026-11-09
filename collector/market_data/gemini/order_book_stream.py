"""Stream Gemini spot best bid/ask over the public market data WebSocket.

Symbols use the same format as the OKX stream (BTC-USD). Run from the collector/ directory:

    python -m market_data.gemini.order_book_stream BTC-USD ETH-USDT
"""

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from websockets.asyncio.client import connect

from ..common.top_of_book import TopOfBook, clean_decimal, split_symbol

log = logging.getLogger(__name__)

EXCHANGE = "Gemini"

# Public market data needs no API keys. One connection per symbol; with
# top_of_book=true Gemini sends only changes to the best bid and ask.
PUBLIC_WS_URL = "wss://api.gemini.com/v1/marketdata"
REST_URL = "https://api.gemini.com"


def to_gemini_symbol(symbol: str) -> str:
    """BTC-USD (or BTC/USD, XBT-USD) -> btcusd, the format Gemini expects."""
    return "".join(split_symbol(symbol)).lower()


async def fetch_pairs(gemini_symbols: list[str], rest_url: str = REST_URL) -> dict[str, tuple[str, str]]:
    """Base and quote asset of each listed symbol, e.g. {"btcusd": ("BTC", "USD")}.

    Symbols Gemini doesn't list are skipped with a warning; it's an error only if none are listed.
    """
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get("/v1/symbols")
        response.raise_for_status()
        listed = set(response.json())
        wanted = [s for s in gemini_symbols if s in listed]
        if missing := [s for s in gemini_symbols if s not in listed]:
            if not wanted:
                raise RuntimeError(f"Gemini has no pair {', '.join(missing)}")
            log.warning("Gemini has no pair %s (skipping it)", ", ".join(missing))

        pairs = {}
        for symbol in wanted:  # Gemini symbols have no separator, so look up base and quote
            details = await client.get(f"/v1/symbols/details/{symbol}")
            details.raise_for_status()
            info = details.json()
            pairs[symbol] = split_symbol(f"{info['base_currency']}-{info['quote_currency']}")
    return pairs


@dataclass
class Side:
    price: Decimal
    quantity: Decimal


class TopTracker:
    """Best bid and ask for one symbol, built from Gemini's top-of-book change events."""

    def __init__(self, instrument_1: str, instrument_2: str) -> None:
        self.instrument_1, self.instrument_2 = instrument_1, instrument_2
        self.sides: dict[str, Side] = {}  # "bid" / "ask"

    def apply(self, message: dict, received_at: datetime) -> TopOfBook | None:
        """Apply an update message; return the new top of book if it changed and both sides are known.

        Update: {"type": "update", "timestampms": ..., "events": [{"type": "change", "side": "bid",
        "price": ..., "remaining": qty at that price, ...}]}. The first one has no timestamp.
        """
        before = dict(self.sides)
        for event in message.get("events", []):
            if event.get("type") == "change":
                self.sides[event["side"]] = Side(clean_decimal(event["price"]), clean_decimal(event["remaining"]))
        if self.sides == before or len(self.sides) < 2:
            return None
        timestamp = (
            datetime.fromtimestamp(message["timestampms"] / 1000, tz=timezone.utc)
            if "timestampms" in message
            else received_at
        )
        bid, ask = self.sides["bid"], self.sides["ask"]
        return TopOfBook(EXCHANGE, self.instrument_1, self.instrument_2, timestamp, bid.price, bid.quantity, ask.price, ask.quantity)


async def stream_symbol(symbol: str, pair: tuple[str, str], url: str = PUBLIC_WS_URL) -> AsyncIterator[TopOfBook]:
    """Top of book for one symbol over its own connection."""
    tracker = TopTracker(*pair)
    async with connect(f"{url}/{symbol}?top_of_book=true&trades=false&auctions=false") as ws:
        print(f"[subscribe] {symbol}")
        async for raw in ws:
            message = json.loads(raw)
            if message.get("type") == "update" and (quote := tracker.apply(message, datetime.now(timezone.utc))):
                yield quote


async def stream_order_books(
    *symbols: str,
    url: str = PUBLIC_WS_URL,
    rest_url: str = REST_URL,
) -> AsyncIterator[TopOfBook]:
    """Yield a TopOfBook whenever any pair's best bid or ask changes (one connection per pair)."""
    pairs = await fetch_pairs([to_gemini_symbol(s) for s in symbols], rest_url)
    queue: asyncio.Queue[TopOfBook | BaseException] = asyncio.Queue()

    async def forward(symbol: str, pair: tuple[str, str]) -> None:
        try:
            async for quote in stream_symbol(symbol, pair, url):
                await queue.put(quote)
            await queue.put(RuntimeError(f"Gemini {symbol} connection closed"))
        except Exception as error:  # hand it to the consumer, which stops the whole stream
            await queue.put(error)

    tasks = [asyncio.create_task(forward(symbol, pair)) for symbol, pair in pairs.items()]
    try:
        while True:
            item = await queue.get()
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        for task in tasks:
            task.cancel()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Pairs, e.g. BTC-USD")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N updates (0 = run forever)")
    args = parser.parse_args()

    count = 0
    async for quote in stream_order_books(*args.symbols):
        print(quote)
        count += 1
        if args.limit and count >= args.limit:
            break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
