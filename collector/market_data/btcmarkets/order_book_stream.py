"""Stream BTC Markets best bid/ask, converted from Australian dollars to the quote you ask for.

BTC Markets' markets are priced in AUD; ask for BTC-USDT and prices are converted with
RateService.convert (USDT_AUD rate, refreshed hourly). Run from the collector/ directory:

    python -m market_data.btcmarkets.order_book_stream BTC-USDT ETH-USDT
"""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime

import httpx
from websockets.asyncio.client import connect

from ..common.local_markets import ConvertedQuotes, best_levels, targets_for
from ..common.top_of_book import TopOfBook

EXCHANGE = "BTC Markets"
LOCAL = "AUD"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://socket.btcmarkets.net/v2"
REST_URL = "https://api.btcmarkets.net"


async def fetch_listed_bases(rest_url: str = REST_URL) -> set[str]:
    """Assets BTC Markets trades against AUD, e.g. {"BTC", "ETH", ...} (markets look like "BTC-AUD")."""
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get("/v3/markets")
    response.raise_for_status()
    return {m["baseAssetName"] for m in response.json() if m["quoteAssetName"] == LOCAL and m["status"] == "Online"}


def parse_orderbook(message: dict) -> tuple[str, tuple | None, datetime]:
    """(market, best levels, time) from an orderbook message (a snapshot of the top 50 levels).

    Message: {"marketId": "BTC-AUD", "timestamp": "2026-11-09T22:06:24.841Z",
    "bids": [[price, size], ...], "asks": [[price, size], ...], "messageType": "orderbook"}.
    """
    top = best_levels(((p, q) for p, q, *_ in message["bids"]), ((p, q) for p, q, *_ in message["asks"]))
    return message["marketId"], top, datetime.fromisoformat(message["timestamp"].replace("Z", "+00:00"))


async def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL, rest_url: str = REST_URL) -> AsyncIterator[TopOfBook]:
    """Yield a converted TopOfBook for each requested pair whenever BTC Markets' best bid or ask changes."""
    targets = await targets_for(EXCHANGE, LOCAL, symbols, await fetch_listed_bases(rest_url))
    converter = ConvertedQuotes(EXCHANGE, targets, lambda t: f"{t.base}-{LOCAL}")

    async with connect(url) as ws:
        request = {"marketIds": converter.markets, "channels": ["orderbook", "heartbeat"], "messageType": "subscribe"}
        await ws.send(json.dumps(request))
        print(f"[subscribe] {', '.join(converter.markets)}")
        async for raw in ws:
            message = json.loads(raw)
            kind = message.get("messageType")
            if kind == "error":
                raise RuntimeError(f"BTC Markets error {message.get('code')}: {message.get('message')}")
            if kind == "orderbook":
                for quote in await converter.quotes(*parse_orderbook(message)):
                    yield quote


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Pairs, e.g. BTC-USDT or BTC-AUD")
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
