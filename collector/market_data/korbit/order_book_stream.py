"""Stream Korbit best bid/ask, converted from Korean won to the quote you ask for.

Korbit's markets are priced in KRW; ask for BTC-USDT and prices are converted with
RateService.convert (USDT_KRW rate, refreshed hourly). Run from the collector/ directory:

    python -m market_data.korbit.order_book_stream BTC-USDT ETH-USDT
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

EXCHANGE = "Korbit"
LOCAL = "KRW"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://ws-api.korbit.co.kr/v2/public"
REST_URL = "https://api.korbit.co.kr"


async def fetch_listed_bases(rest_url: str = REST_URL) -> set[str]:
    """Assets Korbit trades against KRW, e.g. {"BTC", "ETH", ...} (symbols look like "btc_krw")."""
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get("/v2/currencyPairs")
    response.raise_for_status()
    return {
        p["baseCurrency"].upper()
        for p in response.json()["data"]
        if p["quoteCurrency"] == LOCAL.lower() and p["status"] == "launched"
    }


def parse_orderbook(message: dict) -> tuple[str, tuple | None, datetime]:
    """(symbol, best levels, time) from an orderbook message; each message is a full book.

    Message: {"symbol": "btc_krw", "type": "orderbook", "data": {"timestamp": ms,
    "asks": [{"price": ..., "qty": ...}, ...], "bids": [...]}}.
    """
    data = message["data"]
    top = best_levels(((b["price"], b["qty"]) for b in data["bids"]), ((a["price"], a["qty"]) for a in data["asks"]))
    return message["symbol"], top, datetime.fromtimestamp(data["timestamp"] / 1000, tz=timezone.utc)


async def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL, rest_url: str = REST_URL) -> AsyncIterator[TopOfBook]:
    """Yield a converted TopOfBook for each requested pair whenever Korbit's best bid or ask changes."""
    targets = await targets_for(EXCHANGE, LOCAL, symbols, await fetch_listed_bases(rest_url))
    converter = ConvertedQuotes(EXCHANGE, targets, lambda t: f"{t.base.lower()}_{LOCAL.lower()}")

    async with connect(url) as ws:
        await ws.send(json.dumps([{"method": "subscribe", "type": "orderbook", "symbols": converter.markets}]))
        print(f"[subscribe] {', '.join(converter.markets)}")
        async for raw in ws:
            message = json.loads(raw)
            if message.get("status") == "fail":
                raise RuntimeError(f"Korbit error {message.get('code')}: {message.get('message')}")
            if message.get("type") == "orderbook":
                for quote in await converter.quotes(*parse_orderbook(message)):
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
