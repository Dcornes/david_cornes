"""Stream Bitfinex spot best bid/ask over the public WebSocket.

Symbols use the same format as the OKX stream (BTC-USDT). Run from the collector/ directory:

    python -m market_data.bitfinex.order_book_stream BTC-USDT ETH-USD
"""

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from websockets.asyncio.client import connect

from ..common.top_of_book import TopOfBook, split_symbol

log = logging.getLogger(__name__)

EXCHANGE = "Bitfinex"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://api-pub.bitfinex.com/ws/2"
REST_URL = "https://api-pub.bitfinex.com"

# Bitfinex's own codes for some currencies.
BITFINEX_CODES = {"USDT": "UST", "USDC": "UDC"}

TIMESTAMP_FLAG = 32768  # adds the exchange time (ms) to every message
INFO_RECONNECT = 20051  # Bitfinex is restarting the server; reconnect
INFO_MAINTENANCE = 20060  # maintenance started; try again later


def to_bitfinex_pair(symbol: str) -> str:
    """BTC-USDT -> BTCUST; pairs with a longer code use a colon, e.g. AAVE-USD -> AAVE:USD."""
    base, quote = (BITFINEX_CODES.get(asset, asset) for asset in split_symbol(symbol))
    return f"{base}{quote}" if len(base) == 3 and len(quote) == 3 else f"{base}:{quote}"


async def fetch_listed_pairs(rest_url: str = REST_URL) -> set[str]:
    """Every spot pair Bitfinex lists, e.g. {"BTCUSD", "BTCUST", "AAVE:USD", ...}."""
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get("/v2/conf/pub:list:pair:exchange")
    response.raise_for_status()
    return set(response.json()[0])


async def pairs_for(symbols: tuple[str, ...], rest_url: str = REST_URL) -> dict[str, tuple[str, str]]:
    """Bitfinex pair for each listed symbol, e.g. {"BTCUST": ("BTC", "USDT")}."""
    listed = await fetch_listed_pairs(rest_url)
    wanted = {to_bitfinex_pair(s): split_symbol(s) for s in symbols}
    missing = [p for p in wanted if p not in listed]
    if len(missing) == len(wanted):
        raise RuntimeError(f"Bitfinex has no pair {', '.join(missing)}")
    if missing:
        log.warning("Bitfinex has no pair %s (skipping it)", ", ".join(missing))
    return {p: assets for p, assets in wanted.items() if p in listed}


@dataclass
class Level:
    price: Decimal
    quantity: Decimal


class TopBook:
    """Best bid and ask for one pair from a one-level ("len": 1) book channel.

    Entries are [price, count, amount]: amount > 0 is a bid, < 0 an ask; count 0 removes the level.
    """

    def __init__(self, instrument_1: str, instrument_2: str) -> None:
        self.instrument_1, self.instrument_2 = instrument_1, instrument_2
        self.bid: Level | None = None
        self.ask: Level | None = None

    def apply(self, entry: list) -> None:
        price, count, amount = (Decimal(str(x)) for x in entry)
        side = "bid" if amount > 0 else "ask"
        current = getattr(self, side)
        if count == 0:
            if current and current.price == price:
                setattr(self, side, None)  # the next best level follows in its own message
        else:
            setattr(self, side, Level(price, abs(amount)))

    def top_of_book(self, timestamp: datetime) -> TopOfBook | None:
        if not self.bid or not self.ask:
            return None
        return TopOfBook(
            EXCHANGE, self.instrument_1, self.instrument_2, timestamp,
            self.bid.price, self.bid.quantity, self.ask.price, self.ask.quantity,
        )


def message_time(message: list) -> datetime:
    """With the timestamp flag on, the last element is the exchange time in ms."""
    return datetime.fromtimestamp(message[-1] / 1000, tz=timezone.utc)


async def stream_order_books(
    *symbols: str,
    url: str = PUBLIC_WS_URL,
    rest_url: str = REST_URL,
) -> AsyncIterator[TopOfBook]:
    """Subscribe to one-level order books and yield a TopOfBook whenever a pair's best bid or ask changes.

    Pairs Bitfinex doesn't list are skipped with a warning; it's an error only if none are listed.
    """
    pairs = await pairs_for(symbols, rest_url)

    while True:  # Bitfinex sometimes asks clients to reconnect (server restarts)
        books: dict[int, TopBook] = {}  # channel id -> book
        last: dict[int, TopOfBook] = {}
        async with connect(url) as ws:
            await ws.send(json.dumps({"event": "conf", "flags": TIMESTAMP_FLAG}))
            for pair in pairs:
                await ws.send(json.dumps({"event": "subscribe", "channel": "book", "symbol": f"t{pair}", "prec": "P0", "len": 1}))

            async for raw in ws:
                message = json.loads(raw)

                if isinstance(message, dict):
                    event = message.get("event")
                    if event == "subscribed":
                        books[message["chanId"]] = TopBook(*pairs[message["pair"]])
                        print(f"[subscribe] {message['symbol']}")
                    elif event == "error":
                        raise RuntimeError(f"Bitfinex error {message.get('code')}: {message.get('msg')}")
                    elif event == "info" and message.get("code") == INFO_RECONNECT:
                        print("[reconnect] requested by Bitfinex")
                        break
                    elif event == "info" and message.get("code") == INFO_MAINTENANCE:
                        raise RuntimeError("Bitfinex is under maintenance")
                    continue

                channel, data = message[0], message[1]
                book = books.get(channel)
                if book is None or data == "hb":  # heartbeat
                    continue
                for entry in data if isinstance(data[0], list) else [data]:  # snapshot is a list of entries
                    book.apply(entry)
                quote = book.top_of_book(message_time(message))
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
