"""Stream Binance.US spot best bid/ask over the public WebSocket.

Binance.com blocks U.S. connections (HTTP 451), so this uses Binance.US, which
has the same message format. Symbols use the same format as the OKX stream (BTC-USDT).
Run from the collector/ directory:

    python -m market_data.binance.order_book_stream BTC-USDT ETH-USD
"""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx
from websockets.asyncio.client import connect

from ..common.top_of_book import TopOfBook, clean_decimal, split_symbol

EXCHANGE = "Binance.US"

# Public market data needs no API keys. Binance sends ping frames every 20s and
# the websockets library answers them automatically.
PUBLIC_WS_URL = "wss://stream.binance.us:9443/ws"
REST_URL = "https://api.binance.us"


def to_binance_symbol(symbol: str) -> str:
    """BTC-USDT (or BTC/USDT, XBT-USDT) -> BTCUSDT, the format Binance expects."""
    return "".join(split_symbol(symbol))


async def fetch_pairs(binance_symbols: list[str], rest_url: str = REST_URL) -> dict[str, tuple[str, str]]:
    """Look up each symbol's base and quote asset, e.g. {"BTCUSDT": ("BTC", "USDT")}.

    Binance symbols have no separator, so this is the reliable way to split them.
    It also catches unknown symbols, which the WebSocket would silently ignore.
    """
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get(
            "/api/v3/exchangeInfo",
            params={"symbols": json.dumps(binance_symbols, separators=(",", ":"))},
        )
    if response.is_error:
        raise RuntimeError(f"Binance error for {', '.join(binance_symbols)}: {response.json().get('msg')}")
    return {s["symbol"]: (s["baseAsset"], s["quoteAsset"]) for s in response.json()["symbols"]}


def parse_book_ticker(message: dict, pairs: dict[str, tuple[str, str]], received_at: datetime) -> TopOfBook:
    """Turn a bookTicker message into a TopOfBook.

    Message: {"u": update id, "s": "BTCUSDT", "b": bid, "B": bid qty, "a": ask, "A": ask qty}.
    Spot bookTicker has no exchange timestamp, so the time we received it is used.
    """
    instrument_1, instrument_2 = pairs[message["s"]]
    return TopOfBook(
        exchange=EXCHANGE,
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=received_at,
        bid_price=clean_decimal(message["b"]),
        bid_quantity=clean_decimal(message["B"]),
        ask_price=clean_decimal(message["a"]),
        ask_quantity=clean_decimal(message["A"]),
    )


async def stream_order_books(
    *symbols: str,
    url: str = PUBLIC_WS_URL,
    rest_url: str = REST_URL,
) -> AsyncIterator[TopOfBook]:
    """Subscribe to best bid/ask and yield a TopOfBook whenever it changes."""
    pairs = await fetch_pairs([to_binance_symbol(s) for s in symbols], rest_url)
    request = {"method": "SUBSCRIBE", "params": [f"{s.lower()}@bookTicker" for s in pairs], "id": 1}

    async with connect(url) as ws:
        await ws.send(json.dumps(request))

        async for raw in ws:
            received_at = datetime.now(timezone.utc)
            message = json.loads(raw)

            if "error" in message:
                raise RuntimeError(f"Binance error {message['error'].get('code')}: {message['error'].get('msg')}")
            if "id" in message:  # {"result": null, "id": 1} = subscribed
                print(f"[subscribe] {', '.join(request['params'])}")
                continue
            if message.get("s") in pairs:
                yield parse_book_ticker(message, pairs, received_at)


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
