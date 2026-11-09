"""Stream KuCoin spot best bid/ask over the public WebSocket.

KuCoin's WebSocket needs a short-lived token, which its public REST API hands out
without an API key. Symbols use the same format as the OKX stream (BTC-USDT), which
is also KuCoin's own. Run from the collector/ directory:

    python -m market_data.kucoin.order_book_stream BTC-USDT ETH-USDT
"""

import argparse
import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx
from websockets.asyncio.client import connect

from ..common.top_of_book import TopOfBook, clean_decimal, split_symbol

log = logging.getLogger(__name__)

EXCHANGE = "KuCoin"

REST_URL = "https://api.kucoin.com"


def to_kucoin_symbol(symbol: str) -> str:
    """BTC/USDT (or XBT-USDT) -> BTC-USDT, the format KuCoin expects."""
    return "-".join(split_symbol(symbol))


async def listed_symbols(kucoin_symbols: list[str], client: httpx.AsyncClient) -> list[str]:
    """The symbols KuCoin trades. Unknown ones are skipped with a warning; it's an error only if none are left."""
    response = await client.get("/api/v2/symbols")
    response.raise_for_status()
    trading = {s["symbol"] for s in response.json()["data"] if s["enableTrading"]}
    listed = [s for s in kucoin_symbols if s in trading]
    missing = [s for s in kucoin_symbols if s not in trading]
    if not listed:
        raise RuntimeError(f"KuCoin has no pair {', '.join(missing)}")
    if missing:
        log.warning("KuCoin has no pair %s (skipping it)", ", ".join(missing))
    return listed


async def public_connection(client: httpx.AsyncClient) -> tuple[str, float]:
    """WebSocket URL (with a fresh token) and how often to ping, in seconds."""
    response = await client.post("/api/v1/bullet-public")
    response.raise_for_status()
    data = response.json()["data"]
    server = data["instanceServers"][0]
    return f"{server['endpoint']}?token={data['token']}&connectId={uuid.uuid4().hex}", server["pingInterval"] / 1000


def parse_level1(message: dict) -> TopOfBook:
    """Turn a /spotMarket/level1 message into a TopOfBook.

    Message: {"topic": "/spotMarket/level1:BTC-USDT", "data": {"bids": [price, size],
    "asks": [price, size], "timestamp": ms}}.
    """
    instrument_1, instrument_2 = split_symbol(message["topic"].rsplit(":", 1)[1])
    data = message["data"]
    return TopOfBook(
        exchange=EXCHANGE,
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=datetime.fromtimestamp(data["timestamp"] / 1000, tz=timezone.utc),
        bid_price=clean_decimal(data["bids"][0]),
        bid_quantity=clean_decimal(data["bids"][1]),
        ask_price=clean_decimal(data["asks"][0]),
        ask_quantity=clean_decimal(data["asks"][1]),
    )


async def stream_order_books(*symbols: str, rest_url: str = REST_URL) -> AsyncIterator[TopOfBook]:
    """Subscribe to best bid/ask and yield a TopOfBook whenever it changes."""
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        listed = await listed_symbols([to_kucoin_symbol(s) for s in symbols], client)
        url, ping_seconds = await public_connection(client)

    topic = f"/spotMarket/level1:{','.join(listed)}"
    async with connect(url) as ws:
        await ws.send(json.dumps({"id": uuid.uuid4().hex, "type": "subscribe", "topic": topic, "response": True}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=ping_seconds)
            except TimeoutError:  # KuCoin closes connections that don't ping
                await ws.send(json.dumps({"id": uuid.uuid4().hex, "type": "ping"}))
                continue
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "error":
                raise RuntimeError(f"KuCoin error {message.get('code')}: {message.get('data')}")
            if kind == "ack":
                print(f"[subscribe] {', '.join(listed)}")
            elif kind == "message" and message.get("subject") == "level1":
                yield parse_level1(message)


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
