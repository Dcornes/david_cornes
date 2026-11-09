from datetime import datetime, timezone
from decimal import Decimal

from market_data.korbit.order_book_stream import parse_orderbook

# A real (trimmed) Korbit orderbook message; every message is a full book.
MESSAGE = {
    "symbol": "btc_krw", "timestamp": 1794262021156, "type": "orderbook",
    "data": {
        "timestamp": 1794262021127,
        "asks": [{"price": "101757000", "qty": "0.01287236"}, {"price": "101758000", "qty": "0.00918"}],
        "bids": [{"price": "101745000", "qty": "0.5"}, {"price": "101744000", "qty": "0.2"}],
    },
}


def test_parse_orderbook() -> None:
    symbol, top, time = parse_orderbook(MESSAGE)
    assert symbol == "btc_krw"
    assert top == (Decimal("101745000"), Decimal("0.5"), Decimal("101757000"), Decimal("0.01287236"))
    assert time == datetime(2026, 11, 9, 22, 7, 1, 127000, tzinfo=timezone.utc)
