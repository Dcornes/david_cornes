import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from market_data.binance import order_book_stream
from market_data.binance.order_book_stream import clean_decimal, fetch_pairs, parse_book_ticker, to_binance_symbol

MESSAGE = {"u": 3411875888, "s": "BTCUSDT", "b": "74795.60000000", "B": "0.00626000", "a": "74795.61000000", "A": "0.02696000"}
RECEIVED = datetime(2026, 11, 9, 16, 30, tzinfo=timezone.utc)


def test_to_binance_symbol() -> None:
    assert to_binance_symbol("BTC-USDT") == "BTCUSDT"
    assert to_binance_symbol("xbt/usd") == "BTCUSD"


@pytest.mark.parametrize(
    "raw, expected",
    [("74795.60000000", "74795.6"), ("0.00626000", "0.00626"), ("100.00000000", "100"), ("0.00000000", "0")],
)
def test_clean_decimal(raw: str, expected: str) -> None:
    assert str(clean_decimal(raw)) == expected


def test_parse_book_ticker() -> None:
    quote = parse_book_ticker(MESSAGE, {"BTCUSDT": ("BTC", "USDT")}, RECEIVED)

    assert (quote.exchange, quote.symbol, quote.instrument_1, quote.instrument_2) == ("Binance.US", "BTC-USDT", "BTC", "USDT")
    assert (quote.bid_price, quote.bid_quantity) == (Decimal("74795.6"), Decimal("0.00626"))
    assert (quote.ask_price, quote.ask_quantity) == (Decimal("74795.61"), Decimal("0.02696"))
    assert quote.timestamp == RECEIVED
    assert "bid 0.00626 @ 74795.6  |  ask 74795.61 x 0.02696" in str(quote)


def mock_rest(monkeypatch, handler) -> None:
    """Make fetch_pairs talk to a fake Binance REST API."""
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(order_book_stream.httpx, "AsyncClient", fake_client)


def test_fetch_pairs_reads_base_and_quote(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.url.params["symbols"]) == ["BTCUSDT", "ETHUSD"]
        return httpx.Response(200, json={"symbols": [
            {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"},
            {"symbol": "ETHUSD", "baseAsset": "ETH", "quoteAsset": "USD"},
        ]})

    mock_rest(monkeypatch, handler)
    pairs = asyncio.run(fetch_pairs(["BTCUSDT", "ETHUSD"], "https://test"))
    assert pairs == {"BTCUSDT": ("BTC", "USDT"), "ETHUSD": ("ETH", "USD")}


def test_fetch_pairs_unknown_symbol_raises(monkeypatch) -> None:
    mock_rest(monkeypatch, lambda r: httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."}))
    with pytest.raises(RuntimeError, match="NOPEUSD: Invalid symbol"):
        asyncio.run(fetch_pairs(["NOPEUSD"], "https://test"))
