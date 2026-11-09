"""Stream Kraken order book updates over the public v2 WebSocket.

Symbols use the same format as the OKX stream (BTC-USD); XBT is accepted for BTC.
Run from the collector/ directory:

    python -m market_data.kraken.order_book_stream BTC-USD ETH-USD
"""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

from websockets.asyncio.client import connect

from ..common.top_of_book import TopOfBook, split_symbol

EXCHANGE = "Kraken"

# Public market data needs no API keys. Kraken sends a heartbeat every second
# while subscribed, so no keepalive ping is needed.
PUBLIC_WS_URL = "wss://ws.kraken.com/v2"

DEPTH = 10  # levels per side; Kraken allows 10, 25, 100, 500 or 1000


def to_kraken_symbol(symbol: str) -> str:
    """BTC-USD (or XBT-USD, BTC_USD) -> BTC/USD, the format Kraken v2 expects."""
    return "/".join(split_symbol(symbol))


class LocalBook:
    """One symbol's order book, kept up to date from Kraken's snapshot + updates.

    Kraken sends the full book once, then only the levels that change, so the
    best bid/ask has to be worked out from our own copy of the book.
    """

    def __init__(self, depth: int = DEPTH) -> None:
        self.depth = depth
        self.bids: dict[Decimal, Decimal] = {}  # price -> quantity
        self.asks: dict[Decimal, Decimal] = {}

    def apply(self, data: dict, *, snapshot: bool) -> None:
        if snapshot:
            self.bids.clear()
            self.asks.clear()
        for side, levels in ((self.bids, data.get("bids", [])), (self.asks, data.get("asks", []))):
            for level in levels:
                if level["qty"] == 0:
                    side.pop(level["price"], None)  # quantity 0 = level removed
                else:
                    side[level["price"]] = level["qty"]
        # Kraken expects us to drop levels that fall outside the subscribed depth.
        self.bids = dict(sorted(self.bids.items(), reverse=True)[: self.depth])
        self.asks = dict(sorted(self.asks.items())[: self.depth])

    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        return max(self.bids.items()) if self.bids else None

    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        return min(self.asks.items()) if self.asks else None


def to_top_of_book(symbol: str, book: LocalBook, timestamp: str) -> TopOfBook | None:
    """Build a TopOfBook from a LocalBook (None if a side is empty)."""
    bid, ask = book.best_bid(), book.best_ask()
    if bid is None or ask is None:
        return None
    instrument_1, instrument_2 = split_symbol(symbol)
    return TopOfBook(
        exchange=EXCHANGE,
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
        bid_price=bid[0],
        bid_quantity=bid[1],
        ask_price=ask[0],
        ask_quantity=ask[1],
    )


async def stream_order_books(
    *symbols: str,
    depth: int = DEPTH,
    url: str = PUBLIC_WS_URL,
) -> AsyncIterator[TopOfBook]:
    """Subscribe to order books and yield the best bid and ask whenever it changes."""
    request = {
        "method": "subscribe",
        "params": {"channel": "book", "symbol": [to_kraken_symbol(s) for s in symbols], "depth": depth},
        "req_id": 1,
    }
    books: dict[str, LocalBook] = {}
    last_top: dict[str, tuple] = {}

    async with connect(url) as ws:
        await ws.send(json.dumps(request))

        async for raw in ws:
            # Kraken sends prices as JSON numbers; parse them straight to Decimal to keep them exact.
            message = json.loads(raw, parse_float=Decimal, parse_int=Decimal)

            if "method" in message:  # subscribe/unsubscribe acknowledgements
                if not message.get("success"):
                    raise RuntimeError(f"Kraken error: {message.get('error')}")
                print(f"[{message['method']}] {message.get('result', {}).get('symbol')}")
                continue
            if message.get("channel") != "book":
                continue  # heartbeat, status

            for data in message["data"]:
                symbol = data["symbol"]
                book = books.setdefault(symbol, LocalBook(depth))
                book.apply(data, snapshot=message["type"] == "snapshot")

                # Most updates change deeper levels only; skip those.
                top = (book.best_bid(), book.best_ask())
                if top == last_top.get(symbol):
                    continue
                last_top[symbol] = top

                quote = to_top_of_book(symbol, book, data["timestamp"])
                if quote:
                    yield quote


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
