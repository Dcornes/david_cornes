import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from market_data.gemini import order_book_stream
from market_data.gemini.order_book_stream import TopTracker, fetch_pairs, to_gemini_symbol

RECEIVED = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)

# Real messages: the first has both sides and no timestamp; later ones change one side.
INITIAL = {
    "type": "update", "eventId": 1, "socket_sequence": 0,
    "events": [
        {"type": "change", "side": "ask", "price": "74806.71", "remaining": "0.17711517", "delta": "0.17711517", "reason": "initial"},
        {"type": "change", "side": "bid", "price": "74806.7", "remaining": "0.35734296", "delta": "0.35734296", "reason": "initial"},
    ],
}
ASK_CHANGE = {
    "type": "update", "eventId": 2, "socket_sequence": 1, "timestampms": 1794254362205,
    "events": [{"type": "change", "side": "ask", "price": "74806.71", "remaining": "0.177049", "reason": "top-of-book"}],
}


def test_to_gemini_symbol() -> None:
    assert to_gemini_symbol("BTC-USD") == "btcusd"
    assert to_gemini_symbol("xbt/usdt") == "btcusdt"


def test_tracker_builds_top_of_book() -> None:
    tracker = TopTracker("BTC", "USD")

    first = tracker.apply(INITIAL, RECEIVED)
    assert (first.exchange, first.symbol) == ("Gemini", "BTC-USD")
    assert (first.bid_price, first.bid_quantity) == (Decimal("74806.7"), Decimal("0.35734296"))
    assert (first.ask_price, first.ask_quantity) == (Decimal("74806.71"), Decimal("0.17711517"))
    assert first.timestamp == RECEIVED  # the first message has no exchange time

    second = tracker.apply(ASK_CHANGE, RECEIVED)
    assert second.ask_quantity == Decimal("0.177049")
    assert second.bid_price == Decimal("74806.7")  # unchanged side kept
    assert second.timestamp == datetime(2026, 11, 9, 19, 59, 22, 205000, tzinfo=timezone.utc)

    assert tracker.apply(ASK_CHANGE, RECEIVED) is None  # no change, nothing to yield


def test_tracker_waits_for_both_sides() -> None:
    assert TopTracker("BTC", "USD").apply(ASK_CHANGE, RECEIVED) is None


def mock_rest(monkeypatch) -> None:
    """Make fetch_pairs talk to a fake Gemini REST API."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/symbols":
            return httpx.Response(200, json=["btcusd", "ethusdt"])
        symbol = request.url.path.rsplit("/", 1)[1]
        base, quote = {"btcusd": ("BTC", "USD"), "ethusdt": ("ETH", "USDT")}[symbol]
        return httpx.Response(200, json={"symbol": symbol.upper(), "base_currency": base, "quote_currency": quote})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        order_book_stream.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_fetch_pairs_skips_unlisted(monkeypatch) -> None:
    mock_rest(monkeypatch)
    pairs = asyncio.run(fetch_pairs(["btcusd", "ethusdt", "solusdt"], "https://test"))
    assert pairs == {"btcusd": ("BTC", "USD"), "ethusdt": ("ETH", "USDT")}


def test_fetch_pairs_raises_when_none_listed(monkeypatch) -> None:
    mock_rest(monkeypatch)
    with pytest.raises(RuntimeError, match="no pair solusdt"):
        asyncio.run(fetch_pairs(["solusdt"], "https://test"))
