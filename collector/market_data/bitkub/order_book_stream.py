"""Stream Bitkub best bid/ask, converted from Thai baht to the quote you ask for.

Bitkub's markets are priced in THB. Ask for BTC-USDT and the THB prices are converted
with RateService's USDT_THB rate, which refreshes hourly (see common/conversion.py).
BTC-THB gives the unconverted prices. Run from the collector/ directory:

    python -m market_data.bitkub.order_book_stream BTC-USDT ETH-USDT
"""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from websockets.asyncio.client import connect

from ..common.conversion import Target, convert, current_rates, targets_for
from ..common.top_of_book import TopOfBook

EXCHANGE = "Bitkub"
LOCAL = "THB"

# Public market data needs no API keys. Streams are listed in the URL, comma-separated.
PUBLIC_WS_URL = "wss://api.bitkub.com/websocket-api/"
REST_URL = "https://api.bitkub.com"


async def fetch_listed_bases(rest_url: str = REST_URL) -> set[str]:
    """Assets Bitkub trades against THB, e.g. {"BTC", "ETH", ...}."""
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        response = await client.get("/api/v3/market/symbols")
    response.raise_for_status()
    return {
        s["base_asset"]
        for s in response.json()["result"]
        if s["quote_asset"] == LOCAL and s["status"] == "active"
    }


async def stream_order_books(
    *symbols: str,
    url: str = PUBLIC_WS_URL,
    rest_url: str = REST_URL,
) -> AsyncIterator[TopOfBook]:
    """Yield a converted TopOfBook for each requested pair whenever Bitkub's best bid or ask changes."""
    targets = await targets_for(EXCHANGE, LOCAL, symbols, await fetch_listed_bases(rest_url))
    streams: dict[str, list[Target]] = {}  # e.g. "market.ticker.thb_btc" -> BTC-USDT (and BTC-THB, ...)
    for target in targets:
        streams.setdefault(f"market.ticker.thb_{target.base.lower()}", []).append(target)
    last: dict[str, tuple] = {}  # stream -> THB (bid, bid qty, ask, ask qty) last used

    async with connect(url + ",".join(streams)) as ws:
        print(f"[subscribe] {', '.join(streams)}")
        async for raw in ws:
            received_at = datetime.now(timezone.utc)  # Bitkub tickers carry no timestamp
            message = json.loads(raw, parse_float=Decimal)
            stream = message.get("stream")
            if stream not in streams:
                continue
            top = (message["highestBid"], message["highestBidSize"], message["lowestAsk"], message["lowestAskSize"])
            if last.get(stream) == top:
                continue  # ticker also updates for trades and 24h stats
            last[stream] = top
            rates = await current_rates(targets)  # cached; refreshed hourly
            for target in streams[stream]:
                bid, ask = convert(top[0], top[2], target, rates)
                yield TopOfBook(
                    EXCHANGE, target.base, target.quote, received_at, bid, top[1], ask, top[3], target.converted_from
                )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Pairs, e.g. BTC-USDT or BTC-THB")
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
