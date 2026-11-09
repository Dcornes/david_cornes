import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from market_data.kucoin.order_book_stream import listed_symbols, parse_level1, public_connection, to_kucoin_symbol

# A real level1 message.
MESSAGE = {
    "topic": "/spotMarket/level1:BTC-USDT", "type": "message", "subject": "level1",
    "data": {"asks": ["75035.5", "0.5629654"], "bids": ["75035.4", "0.01165987"], "timestamp": 1794261468742},
}


def test_to_kucoin_symbol() -> None:
    assert to_kucoin_symbol("BTC/USDT") == "BTC-USDT"
    assert to_kucoin_symbol("xbt_usdt") == "BTC-USDT"


def test_parse_level1() -> None:
    quote = parse_level1(MESSAGE)

    assert (quote.exchange, quote.symbol) == ("KuCoin", "BTC-USDT")
    assert (quote.bid_price, quote.bid_quantity) == (Decimal("75035.4"), Decimal("0.01165987"))
    assert (quote.ask_price, quote.ask_quantity) == (Decimal("75035.5"), Decimal("0.5629654"))
    assert quote.timestamp == datetime(2026, 11, 9, 21, 57, 48, 742000, tzinfo=timezone.utc)


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://test", transport=httpx.MockTransport(handler))


def test_listed_symbols_skips_unknown_and_disabled() -> None:
    data = [
        {"symbol": "BTC-USDT", "enableTrading": True},
        {"symbol": "ETH-USDT", "enableTrading": True},
        {"symbol": "OLD-USDT", "enableTrading": False},
    ]
    http = client(lambda r: httpx.Response(200, json={"code": "200000", "data": data}))
    assert asyncio.run(listed_symbols(["BTC-USDT", "OLD-USDT", "NOPE-USDT"], http)) == ["BTC-USDT"]
    with pytest.raises(RuntimeError, match="no pair NOPE-USDT"):
        asyncio.run(listed_symbols(["NOPE-USDT"], http))


def test_public_connection_builds_url_with_token() -> None:
    bullet = {"code": "200000", "data": {"token": "abc", "instanceServers": [
        {"endpoint": "wss://ws-api-spot.kucoin.com/", "pingInterval": 18000, "pingTimeout": 10000},
    ]}}
    url, ping_seconds = asyncio.run(public_connection(client(lambda r: httpx.Response(200, json=bullet))))
    assert url.startswith("wss://ws-api-spot.kucoin.com/?token=abc&connectId=")
    assert ping_seconds == 18
