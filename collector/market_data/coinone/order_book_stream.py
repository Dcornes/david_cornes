"""Stream Coinone best bid/ask, converted from Korean won to the quote you ask for.

Coinone's markets are priced in KRW; ask for BTC-USDT and prices are converted with
RateService.convert (USDT_KRW rate, refreshed hourly). Run from the collector/ directory:

    python -m market_data.coinone.order_book_stream BTC-USDT ETH-USDT
"""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx
from websockets.asyncio.client import connect

from ..common.local_markets import ConvertedQuotes, best_levels, targets_for
from ..common.top_of_book import TopOfBook

EXCHANGE = "Coinone"
LOCAL = "KRW"

# Public market data needs no API keys. Coinone closes connections that go quiet.
PUBLIC_WS_URL = "wss://stream.coinone.co.kr"
REST_URL = "https://api.coinone.co.kr"
PING_SECONDS = 60


async def fetch_listed_bases(rest_url: str = REST_URL) -> set[str]:
    """Assets Coinone trades against KRW, e.g. {"BTC", "ETH", ...}."""
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get(f"/public/v2/markets/{LOCAL}")
    response.raise_for_status()
    return {m["target_currency"].upper() for m in response.json()["markets"]}


def parse_orderbook(data: dict) -> tuple[str, tuple | None, datetime]:
    """(base asset, best levels, time) from an ORDERBOOK message's data.

    Data: {"quote_currency": "KRW", "target_currency": "BTC", "timestamp": ms,
    "asks": [{"price": ..., "qty": ...}, ...] (highest first), "bids": [...]}.
    """
    top = best_levels(((b["price"], b["qty"]) for b in data["bids"]), ((a["price"], a["qty"]) for a in data["asks"]))
    return data["target_currency"], top, datetime.fromtimestamp(data["timestamp"] / 1000, tz=timezone.utc)


async def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL, rest_url: str = REST_URL) -> AsyncIterator[TopOfBook]:
    """Yield a converted TopOfBook for each requested pair whenever Coinone's best bid or ask changes."""
    targets = await targets_for(EXCHANGE, LOCAL, symbols, await fetch_listed_bases(rest_url))
    converter = ConvertedQuotes(EXCHANGE, targets, lambda t: t.base)

    async with connect(url) as ws:
        for base in converter.markets:
            topic = {"quote_currency": LOCAL, "target_currency": base}
            await ws.send(json.dumps({"request_type": "SUBSCRIBE", "channel": "ORDERBOOK", "topic": topic}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=PING_SECONDS)
            except TimeoutError:
                await ws.send(json.dumps({"request_type": "PING"}))
                continue
            message = json.loads(raw)
            kind = message.get("response_type")
            if kind == "ERROR":
                raise RuntimeError(f"Coinone error {message.get('error_code')}: {message.get('message')}")
            if kind == "SUBSCRIBED":
                print(f"[subscribe] {message['data']['target_currency']}/{LOCAL}")
            elif kind == "DATA" and message.get("channel") == "ORDERBOOK":
                for quote in await converter.quotes(*parse_orderbook(message["data"])):
                    yield quote


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Pairs, e.g. BTC-USDT or BTC-KRW")
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
