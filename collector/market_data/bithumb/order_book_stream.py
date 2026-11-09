"""Stream Bithumb best bid/ask, converted from Korean won to the quote you ask for.

Bithumb's WebSocket uses the same format as Upbit's, so this reuses the Upbit client
with Bithumb's addresses. KRW prices are converted with RateService's USDT_KRW rate.
Run from the collector/ directory:

    python -m market_data.bithumb.order_book_stream BTC-USDT ETH-USDT
"""

import argparse
import asyncio
from collections.abc import AsyncIterator

from ..common.top_of_book import TopOfBook
from ..upbit.order_book_stream import stream_krw_order_books

EXCHANGE = "Bithumb"

# Public market data needs no API keys.
PUBLIC_WS_URL = "wss://ws-api.bithumb.com/websocket/v1"
REST_URL = "https://api.bithumb.com"


def stream_order_books(*symbols: str, url: str = PUBLIC_WS_URL, rest_url: str = REST_URL) -> AsyncIterator[TopOfBook]:
    """Bithumb best bid/ask for each pair, converted from KRW."""
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
