"""Stream Bitstamp spot best bid/ask over the public WebSocket.

Symbols use the same format as the OKX stream (BTC-USD). Run from the collector/ directory:

    python -m market_data.bitstamp.order_book_stream BTC-USD ETH-USDT
"""

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx
from websockets.asyncio.client import connect

from ..common.top_of_book import TopOfBook, clean_decimal, split_symbol

log = logging.getLogger(__name__)

EXCHANGE = "Bitstamp"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://ws.bitstamp.net"
REST_URL = "https://www.bitstamp.net"


def to_bitstamp_symbol(symbol: str) -> str:
    """BTC-USD (or BTC/USD, XBT-USD) -> btcusd, the format Bitstamp expects."""
    return "".join(split_symbol(symbol)).lower()


async def fetch_pairs(rest_url: str = REST_URL) -> dict[str, tuple[str, str]]:
    """Every pair's base and quote asset, e.g. {"btcusd": ("BTC", "USD")}.

    Bitstamp symbols have no separator, so this is the reliable way to split them.
    It also catches unknown symbols, which the WebSocket would silently accept.
    """
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get("/api/v2/trading-pairs-info/")
    response.raise_for_status()
    return {p["url_symbol"]: split_symbol(p["name"]) for p in response.json()}  # name is "BTC/USD"


async def channels_for(symbols: tuple[str, ...], rest_url: str = REST_URL) -> dict[str, tuple[str, str]]:
    """Order book channel for each listed pair, e.g. {"order_book_btcusd": ("BTC", "USD")}."""
    all_pairs = await fetch_pairs(rest_url)
    wanted = [to_bitstamp_symbol(s) for s in symbols]
    unknown = [s for s in wanted if s not in all_pairs]
    if len(unknown) == len(wanted):
        raise RuntimeError(f"Bitstamp has no pair {', '.join(unknown)}")
    if unknown:
        log.warning("Bitstamp has no pair %s (skipping it)", ", ".join(unknown))
    return {f"order_book_{s}": all_pairs[s] for s in wanted if s in all_pairs}


def parse_order_book(data: dict, instrument_1: str, instrument_2: str) -> TopOfBook | None:
    """Best bid and ask from an order_book message, or None if a side is empty.

    Data: {"microtimestamp": "1794252330371559", "bids": [[price, qty], ...], "asks": [...]},
    best levels first.
    """
    if not data["bids"] or not data["asks"]:
        return None
    (bid_price, bid_quantity), (ask_price, ask_quantity) = data["bids"][0], data["asks"][0]
    return TopOfBook(
        exchange=EXCHANGE,
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=datetime.fromtimestamp(int(data["microtimestamp"]) / 1_000_000, tz=timezone.utc),
        bid_price=clean_decimal(bid_price),
        bid_quantity=clean_decimal(bid_quantity),
        ask_price=clean_decimal(ask_price),
        ask_quantity=clean_decimal(ask_quantity),
    )


async def stream_order_books(
    *symbols: str,
    url: str = PUBLIC_WS_URL,
    rest_url: str = REST_URL,
) -> AsyncIterator[TopOfBook]:
    """Subscribe to order books and yield a TopOfBook whenever a pair's best bid or ask changes.

    Bitstamp sends the top 100 levels on every change; only the first level is used.
    Pairs Bitstamp doesn't list are skipped with a warning; it's an error only if none are listed.
    """
    channels = await channels_for(symbols, rest_url)
    last: dict[str, TopOfBook] = {}  # channel -> last quote yielded

    while True:  # Bitstamp sometimes asks clients to reconnect (e.g. before maintenance)
        async with connect(url) as ws:
            for channel in channels:
                await ws.send(json.dumps({"event": "bts:subscribe", "data": {"channel": channel}}))

            async for raw in ws:
                message = json.loads(raw)
                event, channel = message.get("event"), message.get("channel")

                if event == "bts:request_reconnect":
                    print("[reconnect] requested by Bitstamp")
                    break
                if event == "bts:error":
                    raise RuntimeError(f"Bitstamp error: {message['data'].get('message')}")
                if event == "bts:subscription_succeeded":
                    print(f"[subscribe] {channel}")
                    continue
                if event == "data" and channel in channels:
                    quote = parse_order_book(message["data"], *channels[channel])
                    previous = last.get(channel)
                    if quote and (
                        previous is None
                        or (quote.bid_price, quote.bid_quantity, quote.ask_price, quote.ask_quantity)
                        != (previous.bid_price, previous.bid_quantity, previous.ask_price, previous.ask_quantity)
                    ):
                        last[channel] = quote
                        yield quote
            else:
                return  # connection closed normally without a reconnect request


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
