import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from market_data.upbit import order_book_stream
from market_data.upbit.order_book_stream import best_levels, epoch_time, fetch_listed_bases

# Real messages (trimmed). Upbit prices come in scientific notation; Bithumb's don't.
UPBIT = {
    "type": "orderbook", "code": "KRW-BTC", "timestamp": 1794258991492,
    "orderbook_units": [
        {"ask_price": Decimal("1.01485E8"), "bid_price": Decimal("1.01484E8"), "ask_size": Decimal("0.24174022"), "bid_size": Decimal("0.04814747")},
        {"ask_price": Decimal("1.01562E8"), "bid_price": Decimal("1.01464E8"), "ask_size": Decimal("0.00013842"), "bid_size": Decimal("0.04814747")},
    ],
}
BITHUMB = {
    "type": "orderbook", "code": "KRW-BTC", "timestamp": 1794259077380679, "stream_type": "SNAPSHOT",
    "orderbook_units": [{"ask_price": 101491000, "bid_price": 101460000, "ask_size": Decimal("0.0003"), "bid_size": Decimal("0.0260")}],
}


def test_best_levels_from_first_unit() -> None:
    assert best_levels(UPBIT) == (Decimal("101484000"), Decimal("0.04814747"), Decimal("101485000"), Decimal("0.24174022"))
    assert best_levels(BITHUMB) == (Decimal("101460000"), Decimal("0.026"), Decimal("101491000"), Decimal("0.0003"))
    assert best_levels({"type": "orderbook", "orderbook_units": []}) is None


def test_best_levels_skip_levels_rounded_to_zero() -> None:
    # Real Bithumb message: the best ask level has size 0.0000 (a tiny amount, rounded).
    message = {"type": "orderbook", "code": "KRW-BTC", "orderbook_units": [
        {"ask_price": 101500000, "bid_price": 101460000, "ask_size": Decimal("0.0000"), "bid_size": Decimal("0.0260")},
        {"ask_price": 101503000, "bid_price": 101457000, "ask_size": Decimal("0.2000"), "bid_size": Decimal("0.0169")},
    ]}
    assert best_levels(message) == (Decimal("101460000"), Decimal("0.026"), Decimal("101503000"), Decimal("0.2"))


def test_epoch_time_handles_milliseconds_and_microseconds() -> None:
    assert epoch_time(1794258991492) == datetime(2026, 11, 9, 21, 16, 31, 492000, tzinfo=timezone.utc)  # Upbit
    assert epoch_time(1794259077380679) == datetime(2026, 11, 9, 21, 17, 57, 380679, tzinfo=timezone.utc)  # Bithumb


def test_fetch_listed_bases_keeps_krw_markets(monkeypatch) -> None:
    markets = [{"market": "KRW-BTC"}, {"market": "KRW-ETH"}, {"market": "USDT-BTC"}, {"market": "BTC-XRP"}]
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        order_book_stream.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=markets)), **kwargs),
    )
    assert asyncio.run(fetch_listed_bases("https://test")) == {"BTC", "ETH"}
