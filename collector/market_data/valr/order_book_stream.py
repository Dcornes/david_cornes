"""Stream VALR best bid/ask, converted from South African rand to the quote you ask for.

VALR's markets are priced in ZAR; ask for BTC-USDT and prices are converted with
RateService.convert (USDT_ZAR rate, refreshed hourly). Run from the collector/ directory:

    python -m market_data.valr.order_book_stream BTC-USDT ETH-USDT
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

EXCHANGE = "VALR"
LOCAL = "ZAR"

# Public market data needs no API keys. VALR closes connections that go quiet.
PUBLIC_WS_URL = "wss://api.valr.com/ws/trade"
REST_URL = "https://api.valr.com"
PING_SECONDS = 25


async def fetch_listed_bases(rest_url: str = REST_URL) -> set[str]:
    """Assets VALR trades against ZAR, e.g. {"BTC", "ETH", ...} (pairs look like "BTCZAR").

    VALR's WebSocket ignores unknown pairs without complaint, so they're checked here.
    """
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get("/v1/public/pairs")
    response.raise_for_status()
    return {p["baseCurrency"] for p in response.json() if p["quoteCurrency"] == LOCAL and p["active"]}


def parse_orderbook(message: dict) -> tuple[str, tuple | None, datetime]:
    """(pair, best levels, time) from an AGGREGATED_ORDERBOOK_UPDATE (a full aggregated book).

    Message: {"currencyPairSymbol": "BTCZAR", "data": {"Bids": [{"price": ..., "quantity": ...}, ...],
    "Asks": [...], "LastChange": "2026-11-09T22:07:00.423Z"}}.
    """
    data = message["data"]
    top = best_levels(
        ((b["price"], b["quantity"]) for b in data["Bids"]), ((a["price"], a["quantity"]) for a in data["Asks"])
    )
    return message["currencyPairSymbol"], top, datetime.fromisoformat(data["LastChange"].replace("Z", "+00:00"))


async def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL, rest_url: str = REST_URL) -> AsyncIterator[TopOfBook]:
    """Yield a converted TopOfBook for each requested pair whenever VALR's best bid or ask changes."""
    targets = await targets_for(EXCHANGE, LOCAL, symbols, await fetch_listed_bases(rest_url))
    converter = ConvertedQuotes(EXCHANGE, targets, lambda t: f"{t.base}{LOCAL}")
    subscription = {"event": "AGGREGATED_ORDERBOOK_UPDATE", "pairs": converter.markets}

    async with connect(url) as ws:
        await ws.send(json.dumps({"type": "SUBSCRIBE", "subscriptions": [subscription]}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=PING_SECONDS)
            except TimeoutError:
                await ws.send(json.dumps({"type": "PING"}))
                continue
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "SUBSCRIBED":
                print(f"[subscribe] {message.get('message')}")
            elif kind == "AGGREGATED_ORDERBOOK_UPDATE":
                for quote in await converter.quotes(*parse_orderbook(message)):
                    yield quote
            elif kind and "ERROR" in kind:
                raise RuntimeError(f"VALR error: {message}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Pairs, e.g. BTC-USDT or BTC-ZAR")
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
