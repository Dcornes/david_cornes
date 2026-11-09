"""Stream Upbit best bid/ask, converted from Korean won to the quote you ask for.

Upbit's main markets are priced in KRW. Ask for BTC-USDT and the KRW prices are
converted with RateService.convert (USDT_KRW rate, refreshed hourly).
BTC-KRW gives the unconverted prices.

Bithumb's WebSocket uses the same format, so its client reuses stream_krw_order_books.
Run from the collector/ directory:

    python -m market_data.upbit.order_book_stream BTC-USDT ETH-USDT
"""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from websockets.asyncio.client import connect

from ..common.local_markets import Target, targets_for
from ..common.top_of_book import TopOfBook, clean_decimal
from ..oanda.rate_service import RateService

EXCHANGE = "Upbit"
LOCAL = "KRW"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://api.upbit.com/websocket/v1"
REST_URL = "https://api.upbit.com"


async def fetch_listed_bases(rest_url: str) -> set[str]:
    """Assets traded against KRW, e.g. {"BTC", "ETH", ...} (markets look like "KRW-BTC")."""
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get("/v1/market/all")
    response.raise_for_status()
    return {m["market"].split("-", 1)[1] for m in response.json() if m["market"].startswith(f"{LOCAL}-")}


def best_levels(message: dict) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    """(bid, bid size, ask, ask size): the best level with a real size on each side, or None.

    Message: {"type": "orderbook", "code": "KRW-BTC", "timestamp": ms,
    "orderbook_units": [{"bid_price": 1.01484E8, "bid_size": ..., "ask_price": ..., "ask_size": ...}, ...]}
    Bithumb rounds sizes to 4 decimals, so a level holding a tiny amount shows as 0; those are skipped.
    """
    units = message.get("orderbook_units") or []
    bid = next(((u["bid_price"], u["bid_size"]) for u in units if u["bid_size"] > 0), None)
    ask = next(((u["ask_price"], u["ask_size"]) for u in units if u["ask_size"] > 0), None)
    if bid is None or ask is None:
        return None
    return tuple(clean_decimal(str(x)) for x in (*bid, *ask))


def epoch_time(value: int | Decimal) -> datetime:
    """Upbit sends milliseconds and Bithumb microseconds; tell them apart by size."""
    value = int(value)
    seconds = value / 1_000_000 if value > 10**14 else value / 1000
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def stream_krw_order_books(
    exchange: str,
    ws_url: str,
    rest_url: str,
    *symbols: str,
) -> AsyncIterator[TopOfBook]:
    """Yield a converted TopOfBook for each requested pair whenever the best bid or ask changes."""
    return _stream(exchange, ws_url, rest_url, symbols)


async def _stream(exchange: str, ws_url: str, rest_url: str, symbols: tuple[str, ...]) -> AsyncIterator[TopOfBook]:
    targets = await targets_for(exchange, LOCAL, symbols, await fetch_listed_bases(rest_url))
    markets: dict[str, list[Target]] = {}  # e.g. "KRW-BTC" -> BTC-USDT (and BTC-KRW, ...)
    for target in targets:
        markets.setdefault(f"{LOCAL}-{target.base}", []).append(target)
    last: dict[str, tuple] = {}  # market -> KRW (bid, bid size, ask, ask size) last used

    request = [{"ticket": "david-cornes-markets"}, {"type": "orderbook", "codes": list(markets)}, {"format": "DEFAULT"}]
    async with connect(ws_url) as ws:
        await ws.send(json.dumps(request))
        print(f"[subscribe] {', '.join(markets)}")
        async for raw in ws:  # messages arrive as bytes
            received_at = datetime.now(timezone.utc)
            message = json.loads(raw, parse_float=Decimal)
            if message.get("error"):
                raise RuntimeError(f"{exchange} error: {message['error']}")
            market = message.get("code")
            if message.get("type") != "orderbook" or market not in markets:
                continue
            top = best_levels(message)
            if top is None or last.get(market) == top:
                continue  # deeper levels changed, not the best ones
            last[market] = top
            timestamp = epoch_time(message["timestamp"]) if "timestamp" in message else received_at
            for target in markets[market]:
                # Cached rates (refreshed hourly); in a thread since a refresh makes HTTP requests.
                bid, ask = await asyncio.to_thread(RateService.convert, top[0], top[2], LOCAL, target.quote)
                yield TopOfBook(
                    exchange, target.base, target.quote, timestamp, bid, top[1], ask, top[3], target.converted_from
                )


def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL, rest_url: str = REST_URL) -> AsyncIterator[TopOfBook]:
    """Upbit best bid/ask for each pair, converted from KRW."""
    return stream_krw_order_books(EXCHANGE, url, rest_url, *symbols)


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
