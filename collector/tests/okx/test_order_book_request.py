import json
from datetime import datetime, timezone
from decimal import Decimal

from market_data.common.top_of_book import TopOfBook
from market_data.okx.order_book_stream import parse_top_of_book
from market_data.okx.request_objects.order_book_request import Instrument, OrderBookRequest


def test_subscribe_message_matches_okx_format() -> None:
    request = OrderBookRequest.subscribe("BTC-USDT", "ETH-USDT", channel="books5", id="1")

    assert json.loads(request.to_json()) == {
        "id": "1",
        "op": "subscribe",
        "args": [
            {"channel": "books5", "instId": "BTC-USDT"},
            {"channel": "books5", "instId": "ETH-USDT"},
        ],
    }


def test_id_is_omitted_when_not_set() -> None:
    request = OrderBookRequest(args=[Instrument("books", "BTC-USDT")])
    assert request.to_dict() == {"op": "subscribe", "args": [{"channel": "books", "instId": "BTC-USDT"}]}


def test_unsubscribe() -> None:
    assert OrderBookRequest.unsubscribe("BTC-USDT").to_dict()["op"] == "unsubscribe"


MESSAGE = {
    "arg": {"channel": "books5", "instId": "BTC-USDT"},
    "data": [
        {
            "bids": [["64000.1", "0.5", "0", "3"], ["64000.0", "2", "0", "1"]],
            "asks": [["64000.2", "1.2", "0", "4"], ["64000.3", "3", "0", "2"]],
            "ts": "1762699518261",
        }
    ],
}


def test_parse_top_of_book() -> None:
    quote = parse_top_of_book(MESSAGE)

    assert quote == TopOfBook(
        exchange="OKX",
        instrument_1="BTC",
        instrument_2="USDT",
        timestamp=datetime(2025, 11, 9, 14, 45, 18, 261000, tzinfo=timezone.utc),
        bid_price=Decimal("64000.1"),
        bid_quantity=Decimal("0.5"),
        ask_price=Decimal("64000.2"),
        ask_quantity=Decimal("1.2"),
    )


def test_parse_top_of_book_skips_empty_side() -> None:
    message = {"arg": MESSAGE["arg"], "data": [{"bids": [], "asks": [["1", "1", "0", "1"]], "ts": "1"}]}
    assert parse_top_of_book(message) is None


def test_top_of_book_prints_readably() -> None:
    text = str(parse_top_of_book(MESSAGE))
    assert text.startswith("2025-11-09 14:45:18.261 UTC  OKX")
    assert "BTC-USDT" in text
    assert "bid 0.5 @ 64000.1" in text
    assert "ask 64000.2 x 1.2" in text
