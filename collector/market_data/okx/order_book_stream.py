"""Stream OKX order book updates over a public WebSocket.

Run from the collector/ directory:

    python -m market_data.okx.order_book_stream BTC-USDT ETH-USDT --channel books5
"""

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from websockets.asyncio.client import connect

# Relative imports work whether this runs as market_data.okx... (from collector/)
# or collector.market_data.okx... (from the repo root, e.g. in PyCharm).
from ..common.top_of_book import TopOfBook, split_symbol
from .request_objects.order_book_request import OrderBookRequest

log = logging.getLogger(__name__)

EXCHANGE = "OKX"

# Error code for subscribing to an instrument OKX doesn't list.
UNKNOWN_INSTRUMENT = "60018"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://ws.okx.com:443/ws/v5/public"

# OKX closes connections that are silent for 30s; send "ping" before then.
PING_INTERVAL_SECONDS = 25

# Channels that send full snapshots, so the first level is always the best bid/ask.
# ("books" sends only changed levels after the first message, so it can't be used here.)
TopOfBookChannel = Literal["books5", "bbo-tbt"]


async def stream_order_books(
    *inst_ids: str,
    channel: TopOfBookChannel = "books5",
    url: str = PUBLIC_WS_URL,
) -> AsyncIterator[TopOfBook]:
    """Subscribe to order books and yield the best bid and ask on every update.

    Instruments OKX doesn't list are skipped with a warning; it's an error only if none are listed.
    """
    request = OrderBookRequest.subscribe(*inst_ids, channel=channel, id="orderbook1")
    rejected = 0

    async with connect(url) as ws:
        await ws.send(request.to_json())

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=PING_INTERVAL_SECONDS)
            except TimeoutError:
                await ws.send("ping")
                continue

            if raw == "pong":
                continue

            message = json.loads(raw)
            event = message.get("event")
            if event == "error":
                error = f"OKX error {message.get('code')}: {message.get('msg')}"
                if message.get("code") != UNKNOWN_INSTRUMENT:
                    raise RuntimeError(error)
                rejected += 1
                if rejected == len(inst_ids):
                    raise RuntimeError(error)
                log.warning("%s (skipping it)", error)
                continue
            if event:  # subscribe/unsubscribe acks carry "arg"; notices carry "msg"
                print(f"[{event}] {message.get('arg') or message.get('msg')}")
                continue

            quote = parse_top_of_book(message)
            if quote:
                yield quote


def parse_top_of_book(message: dict) -> TopOfBook | None:
    """Turn an OKX order book message into a TopOfBook (None if a side is empty).

    OKX levels look like ["price", "quantity", "0", "order count"]; ts is epoch milliseconds.
    """
    book = message["data"][0]
    if not book.get("bids") or not book.get("asks"):
        return None
    bid_price, bid_quantity = book["bids"][0][:2]
    ask_price, ask_quantity = book["asks"][0][:2]
    instrument_1, instrument_2 = split_symbol(message["arg"]["instId"])
    return TopOfBook(
        exchange=EXCHANGE,
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=datetime.fromtimestamp(int(book["ts"]) / 1000, tz=timezone.utc),
        bid_price=Decimal(bid_price),
        bid_quantity=Decimal(bid_quantity),
        ask_price=Decimal(ask_price),
        ask_quantity=Decimal(ask_quantity),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inst_ids", nargs="+", help="Instrument ids, e.g. BTC-USDT")
    parser.add_argument("--channel", default="books5", choices=["books5", "bbo-tbt"])
    parser.add_argument("--limit", type=int, default=0, help="Stop after N updates (0 = run forever)")
    args = parser.parse_args()

    count = 0
    async for quote in stream_order_books(*args.inst_ids, channel=args.channel):
        print(quote)
        count += 1
        if args.limit and count >= args.limit:
            break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
