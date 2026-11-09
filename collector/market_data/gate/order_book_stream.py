"""Stream Gate spot best bid/ask over the public WebSocket.

Symbols use the same format as the OKX stream (BTC-USDT). Run from the collector/ directory:

    python -m market_data.gate.order_book_stream BTC-USDT ETH-USDT
"""

import argparse
import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx
from websockets.asyncio.client import connect

from ..common.top_of_book import TopOfBook, clean_decimal, split_symbol

log = logging.getLogger(__name__)

EXCHANGE = "Gate"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://api.gateio.ws/ws/v4/"
REST_URL = "https://api.gateio.ws"


def to_gate_symbol(symbol: str) -> str:
    """BTC-USDT (or BTC/USDT, XBT-USDT) -> BTC_USDT, the format Gate expects."""
    return "_".join(split_symbol(symbol))


async def listed_pairs(gate_symbols: list[str], rest_url: str = REST_URL) -> list[str]:
    """The symbols Gate trades. Unknown ones are skipped with a warning; it's an error only if none are left.

    Gate's WebSocket accepts unknown pairs without complaint, so they're checked here.
    """
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        responses = await asyncio.gather(*(client.get(f"/api/v4/spot/currency_pairs/{s}") for s in gate_symbols))
    listed = [s for s, r in zip(gate_symbols, responses) if r.status_code == 200 and r.json()["trade_status"] == "tradable"]
    missing = [s for s in gate_symbols if s not in listed]
    if not listed:
        raise RuntimeError(f"Gate has no pair {', '.join(missing)}")
    if missing:
        log.warning("Gate has no pair %s (skipping it)", ", ".join(missing))
    return listed


def parse_book_ticker(result: dict) -> TopOfBook:
    """Turn a spot.book_ticker update into a TopOfBook.

    Result: {"t": ms, "u": update id, "s": "BTC_USDT", "b": bid, "B": bid qty, "a": ask, "A": ask qty}.
    """
    instrument_1, instrument_2 = split_symbol(result["s"])
    return TopOfBook(
        exchange=EXCHANGE,
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=datetime.fromtimestamp(result["t"] / 1000, tz=timezone.utc),
        bid_price=clean_decimal(result["b"]),
        bid_quantity=clean_decimal(result["B"]),
        ask_price=clean_decimal(result["a"]),
        ask_quantity=clean_decimal(result["A"]),
    )


async def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL, rest_url: str = REST_URL) -> AsyncIterator[TopOfBook]:
    """Subscribe to best bid/ask and yield a TopOfBook whenever it changes."""
    pairs = await listed_pairs([to_gate_symbol(s) for s in symbols], rest_url)

    async with connect(url) as ws:
        await ws.send(json.dumps({"time": int(time.time()), "channel": "spot.book_ticker", "event": "subscribe", "payload": pairs}))
        async for raw in ws:
            message = json.loads(raw)
            if message.get("error"):
                raise RuntimeError(f"Gate error {message['error'].get('code')}: {message['error'].get('message')}")
            if message.get("event") == "subscribe":
                print(f"[subscribe] {', '.join(pairs)}")
            elif message.get("channel") == "spot.book_ticker" and message.get("event") == "update":
                yield parse_book_ticker(message["result"])


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Pairs, e.g. BTC-USDT")
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
