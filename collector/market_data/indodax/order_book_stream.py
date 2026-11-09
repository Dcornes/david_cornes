"""Best bid/ask from Indodax, converted from Indonesian rupiah to the quote you ask for.

Indodax's public WebSocket needs a token, so this polls its public REST order book
once a second instead (the website shows at most one update a second anyway).
Prices are in IDR; ask for BTC-USDT and they're converted with RateService.convert
(USDT_IDR rate, refreshed hourly). Run from the collector/ directory:

    python -m market_data.indodax.order_book_stream BTC-USDT ETH-USDT
"""

import argparse
import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from ..common.local_markets import ConvertedQuotes, best_levels, targets_for
from ..common.top_of_book import TopOfBook

EXCHANGE = "Indodax"
LOCAL = "IDR"

REST_URL = "https://indodax.com"
POLL_SECONDS = 1.0


async def fetch_listed_bases(client: httpx.AsyncClient) -> set[str]:
    """Assets Indodax trades against IDR, e.g. {"BTC", "ETH", ...} (pair ids look like "btcidr")."""
    response = await client.get("/api/pairs")
    response.raise_for_status()
    return {p["traded_currency"].upper() for p in response.json() if p["base_currency"] == LOCAL.lower()}


def parse_depth(depth: dict) -> tuple | None:
    """Best levels from /api/depth: {"buy": [[price, size], ...], "sell": [[price, size], ...]}."""
    return best_levels(((p, q) for p, q in depth["buy"]), ((p, q) for p, q in depth["sell"]))


async def stream_order_books(*symbols: str, rest_url: str = REST_URL, interval: float = POLL_SECONDS) -> AsyncIterator[TopOfBook]:
    """Yield a converted TopOfBook for each requested pair whenever Indodax's best bid or ask changes."""
    async with httpx.AsyncClient(base_url=rest_url, timeout=10) as client:
        targets = await targets_for(EXCHANGE, LOCAL, symbols, await fetch_listed_bases(client))
        converter = ConvertedQuotes(EXCHANGE, targets, lambda t: f"{t.base.lower()}{LOCAL.lower()}")
        print(f"[poll] {', '.join(converter.markets)} every {interval:g}s")
        while True:
            responses = await asyncio.gather(*(client.get(f"/api/depth/{m}") for m in converter.markets))
            received_at = datetime.now(timezone.utc)  # the depth endpoint has no timestamp
            for market, response in zip(converter.markets, responses):
                response.raise_for_status()
                for quote in await converter.quotes(market, parse_depth(response.json()), received_at):
                    yield quote
            await asyncio.sleep(interval)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Pairs, e.g. BTC-USDT or BTC-IDR")
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
