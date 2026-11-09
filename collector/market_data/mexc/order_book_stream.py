"""Stream MEXC spot best bid/ask over the public WebSocket.

MEXC's WebSocket sends market data as protobuf (a compact binary format). The best
bid/ask message is small, so it's decoded here without a protobuf library. Symbols use
the same format as the OKX stream (BTC-USDT). Run from the collector/ directory:

    python -m market_data.mexc.order_book_stream BTC-USDT ETH-USDT
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

EXCHANGE = "MEXC"

# Public market data needs no API keys. MEXC closes connections that don't ping.
PUBLIC_WS_URL = "wss://wbs-api.mexc.com/ws"
REST_URL = "https://api.mexc.com"
PING_SECONDS = 20

# Best bid/ask, combined over 100ms; {} is the symbol, e.g. BTCUSDT.
CHANNEL = "spot@public.aggre.bookTicker.v3.api.pb@100ms@{}"

# Protobuf field numbers (from MEXC's published .proto files).
WRAPPER_SYMBOL = 3  # PushDataV3ApiWrapper.symbol
WRAPPER_CREATE_TIME = 5  # ms
WRAPPER_SEND_TIME = 6  # ms
WRAPPER_BOOK_TICKER = 315  # PushDataV3ApiWrapper.publicAggreBookTicker
BID_PRICE, BID_QUANTITY, ASK_PRICE, ASK_QUANTITY = 1, 2, 3, 4  # PublicAggreBookTickerV3Api


def to_mexc_symbol(symbol: str) -> str:
    """BTC-USDT (or BTC/USDT, XBT-USDT) -> BTCUSDT, the format MEXC expects."""
    return "".join(split_symbol(symbol))


async def fetch_pairs(mexc_symbols: list[str], rest_url: str = REST_URL) -> dict[str, tuple[str, str]]:
    """Base and quote of each listed symbol, e.g. {"BTCUSDT": ("BTC", "USDT")}.

    MEXC symbols have no separator, so this is the reliable way to split them. Unknown
    symbols are skipped with a warning; it's an error only if none are left.
    """
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        responses = await asyncio.gather(*(client.get("/api/v3/exchangeInfo", params={"symbol": s}) for s in mexc_symbols))
    pairs = {}
    for response in responses:
        for info in response.json().get("symbols", []) if response.status_code == 200 else []:
            pairs[info["symbol"]] = (info["baseAsset"], info["quoteAsset"])
    missing = [s for s in mexc_symbols if s not in pairs]
    if not pairs:
        raise RuntimeError(f"MEXC has no pair {', '.join(missing)}")
    if missing:
        log.warning("MEXC has no pair %s (skipping it)", ", ".join(missing))
    return pairs


def read_varint(data: bytes, position: int) -> tuple[int, int]:
    """Decode a protobuf varint; returns (value, next position)."""
    value = shift = 0
    while True:
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
        shift += 7


def decode_fields(data: bytes) -> dict[int, int | bytes]:
    """Top-level fields of a protobuf message: field number -> int (varint) or bytes (length-delimited)."""
    fields: dict[int, int | bytes] = {}
    position = 0
    while position < len(data):
        key, position = read_varint(data, position)
        number, wire_type = key >> 3, key & 0x7
        if wire_type == 0:  # varint
            fields[number], position = read_varint(data, position)
        elif wire_type == 2:  # length-delimited: strings and nested messages
            length, position = read_varint(data, position)
            fields[number] = data[position : position + length]
            position += length
        elif wire_type == 1:  # 64-bit
            fields[number] = data[position : position + 8]
            position += 8
        elif wire_type == 5:  # 32-bit
            fields[number] = data[position : position + 4]
            position += 4
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire_type}")
    return fields


def parse_book_ticker(data: bytes, pairs: dict[str, tuple[str, str]]) -> TopOfBook | None:
    """Turn a protobuf best bid/ask push into a TopOfBook (None for other messages or symbols)."""
    wrapper = decode_fields(data)
    symbol = wrapper.get(WRAPPER_SYMBOL, b"").decode()
    if WRAPPER_BOOK_TICKER not in wrapper or symbol not in pairs:
        return None
    ticker = decode_fields(wrapper[WRAPPER_BOOK_TICKER])
    millis = wrapper.get(WRAPPER_CREATE_TIME) or wrapper[WRAPPER_SEND_TIME]
    instrument_1, instrument_2 = pairs[symbol]
    return TopOfBook(
        exchange=EXCHANGE,
        instrument_1=instrument_1,
        instrument_2=instrument_2,
        timestamp=datetime.fromtimestamp(millis / 1000, tz=timezone.utc),
        bid_price=clean_decimal(ticker[BID_PRICE].decode()),
        bid_quantity=clean_decimal(ticker[BID_QUANTITY].decode()),
        ask_price=clean_decimal(ticker[ASK_PRICE].decode()),
        ask_quantity=clean_decimal(ticker[ASK_QUANTITY].decode()),
    )


async def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL, rest_url: str = REST_URL) -> AsyncIterator[TopOfBook]:
    """Subscribe to best bid/ask and yield a TopOfBook whenever it changes."""
    pairs = await fetch_pairs([to_mexc_symbol(s) for s in symbols], rest_url)
    channels = [CHANNEL.format(s) for s in pairs]

    async with connect(url) as ws:
        await ws.send(json.dumps({"method": "SUBSCRIPTION", "params": channels}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=PING_SECONDS)
            except TimeoutError:
                await ws.send(json.dumps({"method": "PING"}))
                continue
            if isinstance(raw, bytes):  # market data
                if quote := parse_book_ticker(raw, pairs):
                    yield quote
                continue
            message = json.loads(raw)  # control messages are JSON
            text = message.get("msg", "")
            if text.startswith("Not Subscribed"):
                raise RuntimeError(f"MEXC error: {text}")
            if text.startswith("spot@"):
                print(f"[subscribe] {text}")


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
