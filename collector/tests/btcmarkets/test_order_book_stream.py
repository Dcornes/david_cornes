from datetime import datetime, timezone
from decimal import Decimal

from market_data.btcmarkets.order_book_stream import parse_orderbook

MESSAGE = {
    "marketId": "BTC-AUD", "timestamp": "2026-11-09T22:06:24.841Z", "messageType": "orderbook",
    "bids": [["108132.69", "0.06"], ["108132.68", "0.65890286"]],
    "asks": [["108167.8", "0.25"], ["108170", "1"]],
}


def test_parse_orderbook() -> None:
    market, top, time = parse_orderbook(MESSAGE)
    assert market == "BTC-AUD"
    assert top == (Decimal("108132.69"), Decimal("0.06"), Decimal("108167.8"), Decimal("0.25"))
    assert time == datetime(2026, 11, 9, 22, 6, 24, 841000, tzinfo=timezone.utc)
