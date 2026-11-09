"""Stream Coinbase spot best bid/ask over the public Advanced Trade WebSocket.

Symbols use the same format as the OKX stream (BTC-USD), which is also Coinbase's own.
Run from the collector/ directory:

    python -m market_data.coinbase.order_book_stream BTC-USD ETH-USDT
"""

import argparse
import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime

from websockets.asyncio.client import connect

from ..common.top_of_book import TopOfBook, clean_decimal, split_symbol

log = logging.getLogger(__name__)

EXCHANGE = "Coinbase"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://advanced-trade-ws.coinbase.com"


def to_coinbase_symbol(symbol: str) -> str:
    """BTC/USD (or XBT-USD) -> BTC-USD, the format Coinbase expects."""
    return "-".join(split_symbol(symbol))


def parse_time(value: str) -> datetime:
    """Coinbase times have nanoseconds ("...21.743088571Z"); Python keeps microseconds."""
    return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00")))


def parse_ticker(ticker: dict, timestamp: datetime) -> TopOfBook:
    """Turn one ticker into a TopOfBook.

    Ticker: {"product_id": "BTC-USDT", "best_bid": ..., "best_bid_quantity": ...,
    "best_ask": ..., "best_ask_quantity": ...} plus last price and 24h stats.
    """
    instrument_1, instrument_2 = split_symbol(ticker["product_id"])
    return TopOfBook(
        exchange=EXCHANGE,
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=timestamp,
        bid_price=clean_decimal(ticker["best_bid"]),
        bid_quantity=clean_decimal(ticker["best_bid_quantity"]),
        ask_price=clean_decimal(ticker["best_ask"]),
        ask_quantity=clean_decimal(ticker["best_ask_quantity"]),
    )


def check_subscriptions(requested: list[str], subscribed: list[str]) -> None:
    """Coinbase silently leaves out products it doesn't list. Warn about those; fail if none are left."""
    missing = [p for p in requested if p not in subscribed]
    if len(missing) == len(requested):
        raise RuntimeError(f"Coinbase has no product {', '.join(missing)}")
    if missing:
        log.warning("Coinbase has no product %s (skipping it)", ", ".join(missing))


async def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL) -> AsyncIterator[TopOfBook]:
    """Subscribe to tickers and yield a TopOfBook whenever a pair's best bid or ask changes.

    The ticker also updates for trades and 24h stats; those are skipped unless the top of book moved.
    Products Coinbase doesn't list are skipped with a warning; it's an error only if none are listed.
    """
    products = [to_coinbase_symbol(s) for s in symbols]
    last: dict[str, tuple] = {}  # product -> (bid, bid qty, ask, ask qty) last yielded
    checked = False

    async with connect(url) as ws:
        await ws.send(json.dumps({"type": "subscribe", "product_ids": products, "channel": "ticker"}))
        # Without heartbeats Coinbase closes connections that go quiet.
        await ws.send(json.dumps({"type": "subscribe", "channel": "heartbeats"}))
        print(f"[subscribe] {', '.join(products)}")

        async for raw in ws:
            message = json.loads(raw)
            channel = message.get("channel")

            if message.get("type") == "error":
                raise RuntimeError(f"Coinbase error: {message.get('message')}")
            if channel == "subscriptions" and not checked:
                subscribed = message["events"][0]["subscriptions"].get("ticker")
                if subscribed is not None:
                    check_subscriptions(products, subscribed)
                    checked = True
                continue
            if channel != "ticker":
                continue  # heartbeats

            timestamp = parse_time(message["timestamp"])
            for event in message["events"]:
                for ticker in event["tickers"]:
                    top = (ticker["best_bid"], ticker["best_bid_quantity"], ticker["best_ask"], ticker["best_ask_quantity"])
                    if last.get(ticker["product_id"]) != top:
                        last[ticker["product_id"]] = top
                        yield parse_ticker(ticker, timestamp)


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
