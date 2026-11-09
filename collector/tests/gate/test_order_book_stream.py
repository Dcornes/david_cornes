import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from market_data.gate import order_book_stream
from market_data.gate.order_book_stream import listed_pairs, parse_book_ticker, to_gate_symbol

# A real spot.book_ticker update's result.
RESULT = {"t": 1794261471984, "u": 41079499480, "s": "BTC_USDT", "b": "75029.9", "B": "0.01169", "a": "75030", "A": "0.175174"}


def test_to_gate_symbol() -> None:
    assert to_gate_symbol("BTC-USDT") == "BTC_USDT"
    assert to_gate_symbol("xbt/usdt") == "BTC_USDT"


def test_parse_book_ticker() -> None:
    quote = parse_book_ticker(RESULT)

    assert (quote.exchange, quote.symbol) == ("Gate", "BTC-USDT")
    assert (quote.bid_price, quote.bid_quantity) == (Decimal("75029.9"), Decimal("0.01169"))
    assert (quote.ask_price, quote.ask_quantity) == (Decimal("75030"), Decimal("0.175174"))
    assert quote.timestamp == datetime(2026, 11, 9, 21, 57, 51, 984000, tzinfo=timezone.utc)


def test_listed_pairs_skips_unknown(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.path.rsplit("/", 1)[1]
        if pair == "BTC_USDT":
            return httpx.Response(200, json={"id": pair, "trade_status": "tradable"})
        return httpx.Response(400, json={"label": "INVALID_CURRENCY", "message": "Invalid currency NOPE"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        order_book_stream.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert asyncio.run(listed_pairs(["BTC_USDT", "NOPE_USDT"], "https://test")) == ["BTC_USDT"]
    with pytest.raises(RuntimeError, match="no pair NOPE_USDT"):
        asyncio.run(listed_pairs(["NOPE_USDT"], "https://test"))
