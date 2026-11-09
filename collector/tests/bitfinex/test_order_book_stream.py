import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from market_data.bitfinex import order_book_stream
from market_data.bitfinex.order_book_stream import TopBook, message_time, pairs_for, to_bitfinex_pair

TIME = datetime(2026, 11, 9, 20, 0, 57, 941000, tzinfo=timezone.utc)


def test_to_bitfinex_pair() -> None:
    assert to_bitfinex_pair("BTC-USDT") == "BTCUST"  # Bitfinex calls USDT "UST"
    assert to_bitfinex_pair("xbt/usd") == "BTCUSD"
    assert to_bitfinex_pair("AAVE-USD") == "AAVE:USD"  # longer codes use a colon


def test_message_time_is_last_element() -> None:
    assert message_time([224741, [74922, 0, 1], 1794254457941]) == TIME


def test_top_book_from_snapshot_and_updates() -> None:
    book = TopBook("BTC", "USDT")
    for entry in [[74922, 2, 0.08958717], [74933, 1, -0.0668046]]:  # snapshot
        book.apply(entry)

    quote = book.top_of_book(TIME)
    assert (quote.exchange, quote.symbol) == ("Bitfinex", "BTC-USDT")
    assert (quote.bid_price, quote.bid_quantity) == (Decimal("74922"), Decimal("0.08958717"))
    assert (quote.ask_price, quote.ask_quantity) == (Decimal("74933"), Decimal("0.0668046"))  # asks are negative

    book.apply([74922, 0, 1])  # best bid removed...
    assert book.top_of_book(TIME) is None  # ...wait for the next level
    book.apply([74921, 3, 1.5])
    assert book.top_of_book(TIME).bid_price == Decimal("74921")


def test_removal_of_another_price_is_ignored() -> None:
    book = TopBook("BTC", "USDT")
    book.apply([100, 1, 1])
    book.apply([101, 1, -1])
    book.apply([102, 1, -2])  # better ask replaces the old one
    book.apply([101, 0, -1])  # late removal of the old ask
    assert book.top_of_book(TIME).ask_price == Decimal("102")


def mock_rest(monkeypatch) -> None:
    real_client = httpx.AsyncClient
    listed = [["BTCUSD", "BTCUST", "AAVE:USD"]]
    monkeypatch.setattr(
        order_book_stream.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=listed)), **kwargs),
    )


def test_pairs_for_skips_unlisted(monkeypatch) -> None:
    mock_rest(monkeypatch)
    pairs = asyncio.run(pairs_for(("BTC-USDT", "NOPE-USD"), "https://test"))
    assert pairs == {"BTCUST": ("BTC", "USDT")}  # our names, not Bitfinex's


def test_pairs_for_raises_when_none_listed(monkeypatch) -> None:
    mock_rest(monkeypatch)
    with pytest.raises(RuntimeError, match="no pair NOPE:USD"):
        asyncio.run(pairs_for(("NOPE-USD",), "https://test"))
