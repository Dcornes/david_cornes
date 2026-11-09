import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from market_data.bitstamp import order_book_stream
from market_data.bitstamp.order_book_stream import channels_for, fetch_pairs, parse_order_book, to_bitstamp_symbol

DATA = {
    "timestamp": "1794252330",
    "microtimestamp": "1794252330371559",
    "bids": [["74591.56", "0.77000000"], ["74589.91", "0.06703318"]],
    "asks": [["74591.57", "0.12500000"], ["74592.00", "1.00000000"]],
}

PAIRS = [
    {"name": "BTC/USD", "url_symbol": "btcusd"},
    {"name": "ETH/USDT", "url_symbol": "ethusdt"},
]


def test_to_bitstamp_symbol() -> None:
    assert to_bitstamp_symbol("BTC-USD") == "btcusd"
    assert to_bitstamp_symbol("xbt/usdt") == "btcusdt"


def test_parse_order_book_uses_first_level() -> None:
    quote = parse_order_book(DATA, "BTC", "USD")

    assert (quote.exchange, quote.symbol) == ("Bitstamp", "BTC-USD")
    assert (quote.bid_price, quote.bid_quantity) == (Decimal("74591.56"), Decimal("0.77"))
    assert (quote.ask_price, quote.ask_quantity) == (Decimal("74591.57"), Decimal("0.125"))
    assert quote.timestamp == datetime(2026, 11, 9, 19, 25, 30, 371559, tzinfo=timezone.utc)


def test_parse_order_book_empty_side_returns_none() -> None:
    assert parse_order_book({**DATA, "asks": []}, "BTC", "USD") is None


def mock_rest(monkeypatch) -> None:
    """Make fetch_pairs talk to a fake Bitstamp REST API."""
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        return real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAIRS)), **kwargs)

    monkeypatch.setattr(order_book_stream.httpx, "AsyncClient", fake_client)


def test_fetch_pairs_reads_base_and_quote(monkeypatch) -> None:
    mock_rest(monkeypatch)
    assert asyncio.run(fetch_pairs("https://test")) == {"btcusd": ("BTC", "USD"), "ethusdt": ("ETH", "USDT")}


def test_channels_for_skips_unlisted_pairs(monkeypatch) -> None:
    mock_rest(monkeypatch)
    channels = asyncio.run(channels_for(("BTC-USD", "SOL-USDT"), "https://test"))
    assert channels == {"order_book_btcusd": ("BTC", "USD")}


def test_channels_for_raises_when_no_pair_is_listed(monkeypatch) -> None:
    mock_rest(monkeypatch)
    with pytest.raises(RuntimeError, match="no pair solusdt"):
        asyncio.run(channels_for(("SOL-USDT",), "https://test"))
