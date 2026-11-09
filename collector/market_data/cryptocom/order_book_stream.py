"""Stream Crypto.com Exchange spot best bid/ask over the public WebSocket.

Symbols use the same format as the OKX stream (BTC-USDT). Run from the collector/ directory:

    python -m market_data.cryptocom.order_book_stream BTC-USDT ETH-USD
"""

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from websockets.asyncio.client import connect

from ..common.top_of_book import TopOfBook, clean_decimal, split_symbol

log = logging.getLogger(__name__)

EXCHANGE = "Crypto.com"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://stream.crypto.com/exchange/v1/market"

# Crypto.com counts rate limits from when the connection opens, so it asks
# clients to wait a moment before sending the first request.
CONNECT_DELAY_SECONDS = 1


def to_cryptocom_symbol(symbol: str) -> str:
    """BTC-USDT (or BTC/USDT, XBT-USDT) -> BTC_USDT, the format Crypto.com expects."""
    return "_".join(split_symbol(symbol))


def parse_ticker(ticker: dict) -> TopOfBook:
    """Turn one ticker entry into a TopOfBook.

    Entry: {"i": "BTC_USDT", "b": bid, "bs": bid qty, "k": ask, "ks": ask qty, "t": ms, ...}
    (plus 24h high/low/volume, which aren't needed here).
    """
    instrument_1, instrument_2 = split_symbol(ticker["i"])
    return TopOfBook(
        exchange=EXCHANGE,
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=datetime.fromtimestamp(ticker["t"] / 1000, tz=timezone.utc),
        bid_price=clean_decimal(ticker["b"]),
        bid_quantity=clean_decimal(ticker["bs"]),
        ask_price=clean_decimal(ticker["k"]),
        ask_quantity=clean_decimal(ticker["ks"]),
    )


def heartbeat_reply(message: dict) -> dict | None:
    """Crypto.com closes the connection unless every heartbeat is answered."""
    if message.get("method") == "public/heartbeat":
        return {"id": message["id"], "method": "public/respond-heartbeat"}
    return None


async def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL) -> AsyncIterator[TopOfBook]:
    """Subscribe to tickers and yield a TopOfBook whenever a pair's best bid or ask changes.

    The ticker also updates for trades and 24h stats; those are skipped unless the top of book moved.
    Pairs Crypto.com doesn't list are skipped with a warning; it's an error only if none are listed.
    """
    channels = [f"ticker.{to_cryptocom_symbol(s)}" for s in symbols]
    request = {"id": 1, "method": "subscribe", "params": {"channels": channels}}
    active = set(channels)  # channels not rejected by Crypto.com
    last: dict[str, tuple] = {}  # instrument -> (bid, bid qty, ask, ask qty) last yielded

    async with connect(url) as ws:
        await asyncio.sleep(CONNECT_DELAY_SECONDS)
        await ws.send(json.dumps(request))
        print(f"[subscribe] {', '.join(channels)}")

        async for raw in ws:
            message = json.loads(raw)

            if reply := heartbeat_reply(message):
                await ws.send(json.dumps(reply))
                continue
            if message.get("code"):  # e.g. 40003 Unknown symbol: skip that pair, keep the others
                error = f"Crypto.com error {message['code']} for {message.get('channel')}: {message.get('message')}"
                active.discard(message.get("channel"))
                if not active:
                    raise RuntimeError(error)
                log.warning("%s (skipping it)", error)
                continue

            result = message.get("result") or {}
            if result.get("channel") != "ticker":
                continue
            for ticker in result["data"]:
                top = (ticker["b"], ticker["bs"], ticker["k"], ticker["ks"])
                if last.get(ticker["i"]) != top:
                    last[ticker["i"]] = top
                    yield parse_ticker(ticker)


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
